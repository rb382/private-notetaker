"""Turns a recording into a transcript and notes, then deletes the audio.

Nothing here sends audio off this computer. The only network call is to a local
Ollama server (127.0.0.1) for the notes, and it receives text only.
"""
import datetime as dt
import hashlib
import json
import os
import platform
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

_model_cache: dict = {}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def delete_audio(path: Path) -> None:
    """Overwrites the file with zeros, then removes it.

    On SSDs the drive may keep old copies internally, so this is a best effort;
    full-disk encryption (BitLocker) is what protects deleted data properly.
    """
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            zeros = b"\0" * (1 << 20)
            left = size
            while left > 0:
                n = min(left, len(zeros))
                f.write(zeros[:n])
                left -= n
            f.flush()
            os.fsync(f.fileno())
    finally:
        if path.exists():
            path.unlink()


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def transcribe(path: Path, model_name: str, language: str | None, progress=lambda p: None) -> tuple[list[dict], dict]:
    if model_name.startswith("parakeet"):
        return _transcribe_parakeet(path, progress)
    from faster_whisper import WhisperModel  # imported here so the app starts even if it is still installing

    model = _model_cache.get(model_name)
    if model is None:
        model = WhisperModel(model_name, device="auto", compute_type="int8")
        _model_cache.clear()
        _model_cache[model_name] = model
    lang = None if language in (None, "", "auto") else language
    segments, info = model.transcribe(str(path), language=lang, vad_filter=True, beam_size=5)
    out = []
    total = max(float(info.duration or 0), 0.01)
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        progress(min(0.99, seg.end / total))
    progress(1.0)
    return out, {"language": info.language, "duration": info.duration}


def _transcribe_parakeet(path: Path, progress) -> tuple[list[dict], dict]:
    """NVIDIA Parakeet TDT 0.6B v3 on CPU via onnx-asr. Needs: pip install "onnx-asr[cpu,hub]"."""
    try:
        import onnx_asr
    except ImportError as exc:
        raise RuntimeError('Parakeet is not installed. Run setup with --parakeet, or: pip install "onnx-asr[cpu,hub]"') from exc
    from faster_whisper.audio import decode_audio  # decodes any format to 16 kHz mono

    audio = decode_audio(str(path), sampling_rate=16000)
    total = max(len(audio) / 16000, 0.01)
    model = _model_cache.get("parakeet-v3")
    if model is None:
        try:
            asr = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
        except Exception:
            asr = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")
        model = asr.with_vad(onnx_asr.load_vad("silero"))
        _model_cache.clear()
        _model_cache["parakeet-v3"] = model
    out = []
    for seg in model.recognize(audio, sample_rate=16000):
        text = (seg.text or "").strip()
        if text:
            out.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": text})
        progress(min(0.99, float(seg.end) / total))
    progress(1.0)
    return out, {"language": None, "duration": total}


NOTES_PROMPT = """You write meeting notes from a transcript.
Use only what is in the transcript. Do not invent names, numbers, decisions or deadlines.
If a section has nothing, write "None stated."
Reply in Markdown with exactly these three sections:

## Key points
- ...

## Decisions
- ...

## Actions
- [action] (owner if stated, deadline if stated)
"""


def _ollama_chat(url: str, model: str, system: str, user: str, timeout: int = 600) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "options": {"num_ctx": 16384, "temperature": 0.2},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["message"]["content"].strip()


def ollama_available(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def write_notes(transcript_text: str, url: str, model: str) -> str:
    # Long meetings: summarise in parts, then combine the parts.
    limit = 30000
    if len(transcript_text) <= limit:
        return _ollama_chat(url, model, NOTES_PROMPT, transcript_text)
    parts, buf = [], ""
    for line in transcript_text.splitlines(keepends=True):
        if len(buf) + len(line) > limit and buf:
            parts.append(buf)
            buf = ""
        buf += line
    if buf:
        parts.append(buf)
    partial = [_ollama_chat(url, model, NOTES_PROMPT, p) for p in parts]
    combined = "\n\n".join(f"Notes from part {i + 1}:\n{p}" for i, p in enumerate(partial))
    return _ollama_chat(url, model, NOTES_PROMPT + "\nYou are given notes from consecutive parts of one meeting. Merge them into one set and remove repeats.", combined)


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40]
    return s or "meeting"


def run(audio: Path, meeting_dir: Path, meta: dict, cfg: dict, status: dict) -> None:
    """Runs the whole pipeline and records each step in receipt.json."""
    receipt = {
        "app": "notetaker 0.1",
        "title": meta["title"],
        "started_at": meta["started_at"],
        "ended_at": meta["ended_at"],
        "length_seconds": round(meta["seconds"], 1) if meta.get("seconds") else None,
        "consent": meta["consent"],
        "processed_on": f"this computer ({platform.node()})",
        "audio_sent_over_network": False,
        "audio_sha256": None,
        "speech_model": cfg["speech_model"],
        "notes_model": None,
        "audio_deleted_at": None,
        "steps": [],
    }

    def step(msg: str) -> None:
        receipt["steps"].append({"at": _now(), "what": msg})
        status["message"] = msg

    try:
        receipt["audio_sha256"] = sha256_file(audio)
        step(f"Transcribing on this computer with {cfg['speech_model']}. The first run downloads the model once.")
        t0 = time.monotonic()
        segs, info = transcribe(audio, cfg["speech_model"], cfg.get("language"),
                                progress=lambda p: status.__setitem__("progress", round(p, 3)))
        took = time.monotonic() - t0
        dur = float(info.get("duration") or meta.get("seconds") or 0)
        if not receipt["length_seconds"]:
            receipt["length_seconds"] = round(dur, 1)
        receipt["detected_language"] = info.get("language")
        receipt["timing"] = {"audio_seconds": round(dur, 1), "transcribe_seconds": round(took, 1),
                             "seconds_per_audio_minute": round(took / (dur / 60), 1) if dur else None}
        step(f"Transcribed {len(segs)} segments in {took:.0f} s.")
    finally:
        if audio.exists():
            delete_audio(audio)
            receipt["audio_deleted_at"] = _now()
            step("Audio overwritten and deleted.")

    lines = [f"[{fmt_ts(s['start'])}] {s['text']}" for s in segs]
    transcript_md = f"# Transcript: {meta['title']}\n\n{meta['started_at']}\n\n" + "\n".join(lines) + "\n"
    (meeting_dir / "transcript.md").write_text(transcript_md, encoding="utf-8")
    (meeting_dir / "transcript.json").write_text(json.dumps(segs, indent=1), encoding="utf-8")

    notes_md = None
    if not segs:
        step("No speech found, so no notes were written.")
    elif not cfg.get("write_notes", True):
        step("Notes skipped by request.")
    elif ollama_available(cfg["ollama_url"]):
        status["progress"] = None
        step(f"Writing notes on this computer with {cfg['notes_model']}.")
        try:
            notes_md = write_notes("\n".join(lines), cfg["ollama_url"], cfg["notes_model"])
            receipt["notes_model"] = cfg["notes_model"]
            step("Notes written.")
        except Exception as exc:  # model missing, out of memory, timeout
            step(f"Notes could not be written: {exc}. The transcript is saved.")
    else:
        step("Notes skipped: Ollama is not running on this computer. The transcript is saved.")

    if notes_md:
        (meeting_dir / "notes.md").write_text(f"# Notes: {meta['title']}\n\n{meta['started_at']}\n\n{notes_md}\n", encoding="utf-8")

    receipt["finished_at"] = _now()
    (meeting_dir / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    # Export for Calimero: always write meeting.json; push to a context only if switched on.
    from . import calimero
    payload = calimero.build_payload(meeting_dir.name, meta, segs, notes_md, receipt)
    (meeting_dir / "meeting.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    if cfg.get("calimero", {}).get("enabled"):
        step("Sending the transcript and notes to the Calimero context.")
        try:
            calimero.push(payload, cfg["calimero"])
            step("Stored in the Calimero context.")
        except Exception as exc:
            step(f"Could not store in Calimero: {exc}. Everything is still saved on this computer.")
        (meeting_dir / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
