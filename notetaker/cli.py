"""Command line, for testing without a microphone (for example on a Calimero job node).

  python -m notetaker.cli process meeting.wav --model small.en
  python -m notetaker.cli process meeting.m4a --model parakeet-v3 --no-notes --out /tmp/notes
  python -m notetaker.cli push ~/PrivateNotes/2026-11-20_1000_Weekly-sync
  python -m notetaker.cli serve

`process` works on a temporary copy, so your original file is never deleted.
It prints a JSON summary with timings and where the output was written.
"""
import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import calimero, config, pipeline


def cmd_process(a) -> int:
    src = Path(a.audio).expanduser()
    if not src.is_file():
        print(f"No such file: {src}", file=sys.stderr)
        return 2
    cfg = config.load()
    if a.model:
        if a.model not in config.SPEECH_MODELS:
            print(f"Unknown model {a.model}. Choose from: {', '.join(config.SPEECH_MODELS)}", file=sys.stderr)
            return 2
        cfg["speech_model"] = a.model
    if a.language:
        cfg["language"] = a.language
    if a.no_notes:
        cfg["write_notes"] = False
    if a.out:
        cfg["notes_dir"] = a.out
    if a.calimero:
        cfg["calimero"]["enabled"] = True

    now = dt.datetime.now().astimezone()
    title = a.title or src.stem
    folder = config.notes_dir(cfg) / f"{now:%Y-%m-%d_%H%M%S}_{pipeline.slug(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="notetaker_"))
    work = tmpdir / ("input" + src.suffix.lower())
    shutil.copyfile(src, work)  # the pipeline deletes this copy, never the original
    meta = {"title": title, "started_at": now.isoformat(timespec="seconds"),
            "ended_at": now.isoformat(timespec="seconds"), "seconds": None,
            "consent": {"confirmed_by_host": None, "note": "processed from a file with the command line"}}
    status: dict = {}
    try:
        pipeline.run(work, folder, meta, cfg, status)
    finally:
        if work.exists():
            pipeline.delete_audio(work)
        shutil.rmtree(tmpdir, ignore_errors=True)
    receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    summary = {"output": str(folder), "speech_model": cfg["speech_model"], "timing": receipt.get("timing"),
               "segments": len(json.loads((folder / "transcript.json").read_text(encoding="utf-8"))),
               "notes": (folder / "notes.md").exists(), "last_step": receipt["steps"][-1]["what"]}
    print(json.dumps(summary, indent=2))
    return 0


def cmd_push(a) -> int:
    folder = Path(a.meeting_dir).expanduser()
    payload = json.loads((folder / "meeting.json").read_text(encoding="utf-8"))
    cfg = config.load()
    print(calimero.push(payload, cfg["calimero"]) or "Sent.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m notetaker.cli", description="Private Notetaker command line")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process", help="Transcribe an audio file (wav, mp3, m4a...) and write notes")
    p.add_argument("audio")
    p.add_argument("--model", help="Speech model: " + ", ".join(config.SPEECH_MODELS))
    p.add_argument("--language", help="Language code, e.g. en, or auto")
    p.add_argument("--title")
    p.add_argument("--out", help="Folder for the output (default: ~/PrivateNotes)")
    p.add_argument("--no-notes", action="store_true", help="Skip the Ollama notes step")
    p.add_argument("--calimero", action="store_true", help="Also push the result to the configured Calimero context")
    p.set_defaults(fn=cmd_process)
    q = sub.add_parser("push", help="Send an existing meeting folder's meeting.json to Calimero")
    q.add_argument("meeting_dir")
    q.set_defaults(fn=cmd_push)
    r = sub.add_parser("serve", help="Start the app in the browser")
    r.set_defaults(fn=lambda a: __import__("notetaker.app", fromlist=["main"]).main() or 0)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
