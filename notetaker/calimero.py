"""Hands a finished meeting to Calimero.

The meeting is described by one JSON object (schema "notetaker.meeting/1", see HANDOVER.md).
It is always written to meeting.json in the meeting's folder. If the calimero setting is enabled,
it is also sent to a context by calling an app method through meroctl, for example:

    meroctl --node <node> call add_meeting --context <context_id> --as <member_key> --args '{"meeting": {...}}'

Swap push() for Calimero's Python or JS client if you prefer; the payload stays the same.
"""
import json
import shutil
import subprocess

SCHEMA = "notetaker.meeting/1"


def build_payload(meeting_id: str, meta: dict, segments: list[dict], notes_md: str | None, receipt: dict) -> dict:
    return {
        "schema": SCHEMA,
        "id": meeting_id,
        "title": meta.get("title"),
        "started_at": meta.get("started_at"),
        "ended_at": meta.get("ended_at"),
        "length_seconds": receipt.get("length_seconds"),
        "language": receipt.get("detected_language"),
        "transcript": [{"start": s["start"], "end": s["end"], "speaker": s.get("speaker"), "text": s["text"]} for s in segments],
        "notes_md": notes_md,
        "receipt": receipt,
    }


def push(payload: dict, cal: dict, timeout: int = 60) -> str:
    missing = [k for k in ("node", "context_id", "member_key", "method") if not cal.get(k)]
    if missing:
        raise RuntimeError("calimero settings missing: " + ", ".join(missing))
    exe = shutil.which(cal.get("meroctl") or "meroctl")
    if not exe:
        raise RuntimeError("meroctl not found on PATH")
    cmd = [exe, "--node", cal["node"], "call", cal["method"],
           "--context", cal["context_id"], "--as", cal["member_key"],
           "--args", json.dumps({"meeting": payload}, separators=(",", ":"))]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"meroctl exit {r.returncode}").strip()[:500])
    return r.stdout.strip()
