"""Hands a finished meeting to Calimero.

The meeting is described by one JSON object (schema "notetaker.meeting/1", see HANDOVER.md).
It is always written to meeting.json in the meeting's folder. If the calimero setting is
enabled, it is also stored in an instance of the meetings app (calimero-app/ in this repo)
by calling its addMeeting method. Only text is sent: the audio was deleted before this runs.

Two ways to send it, both through meroctl's attested, sealed channel and its saved session
(sign in once with `meroctl login` or `meroctl cloud login`):

- "proxy" (recommended): run `meroctl proxy` (it listens on 127.0.0.1:2580) and the app
  POSTs the call to it. Any length of meeting works.
- "meroctl": the app runs `meroctl call --instance <instance> addMeeting --args <json>`.
  The arguments travel on the command line, which limits a meeting to about 100 KB of
  JSON (about 30 KB on Windows), roughly 20 or 5 minutes of talk.
"""
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SCHEMA = "notetaker.meeting/1"

# Command-line limits: Linux refuses one argument over 128 KiB, Windows a whole line over 32 KiB.
ARGV_LIMIT = 30_000 if sys.platform.startswith("win") else 120_000
# The node refuses call arguments over 512 KiB.
NODE_LIMIT = 512 * 1024


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


def app_args(payload: dict, participants: list[str] | None = None) -> dict:
    """The meetings app's addMeeting arguments for a meeting.json payload."""
    meeting = {
        "id": str(payload["id"]),
        "title": payload.get("title") or "Meeting",
        "startedAt": payload.get("started_at") or "",
        "endedAt": payload.get("ended_at") or "",
        "lengthSeconds": float(payload.get("length_seconds") or 0.0),
        "language": payload.get("language") or "",
        "transcript": [
            {"start": float(s["start"]), "end": float(s["end"]), "speaker": s.get("speaker"), "text": s["text"]}
            for s in payload.get("transcript") or []
        ],
        "notesMd": payload.get("notes_md") or "",
        "receiptJson": json.dumps(payload.get("receipt") or {}, separators=(",", ":"), sort_keys=True),
    }
    return {"meeting": meeting, "participants": [p for p in (participants or []) if p]}


def push(payload: dict, cal: dict, timeout: int = 60) -> str:
    instance = cal.get("instance")
    if not instance:
        raise RuntimeError("calimero settings missing: instance")
    method = cal.get("method") or "addMeeting"
    if method == "add_meeting":  # the name 0.2 used before the app existed
        method = "addMeeting"
    args = app_args(payload, cal.get("participants"))
    body = json.dumps(args, separators=(",", ":"))
    if len(body.encode()) > NODE_LIMIT:
        raise RuntimeError(f"this meeting is {len(body)//1024} KB; the node takes at most 512 KB per call")
    if cal.get("mode", "proxy") == "proxy":
        return _via_proxy(cal.get("proxy_url") or "http://127.0.0.1:2580", instance, method, args, payload["id"], timeout)
    return _via_meroctl(cal, instance, method, body, payload["id"], timeout)


def _via_proxy(base: str, instance: str, method: str, args: dict, key: str, timeout: int) -> str:
    url = f"{base.rstrip('/')}/apps/{instance}/intents"
    req = urllib.request.Request(
        url,
        data=json.dumps({"method": method, "args": args, "idempotencyKey": f"meeting-{key}"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").strip()[:500]
        raise RuntimeError(f"Calimero answered {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach meroctl proxy at {base} ({e.reason}); is `meroctl proxy` running?") from None


def _via_meroctl(cal: dict, instance: str, method: str, body: str, key: str, timeout: int) -> str:
    if len(body) > ARGV_LIMIT:
        raise RuntimeError(f"this meeting is {len(body)//1024} KB, too long for the meroctl command line; "
                           "use mode \"proxy\" with `meroctl proxy` running")
    exe = shutil.which(cal.get("meroctl") or "meroctl")
    if not exe:
        raise RuntimeError("meroctl not found on PATH")
    cmd = [exe]
    if cal.get("node"):
        cmd += ["--node", cal["node"]]
    cmd += ["call", "--instance", instance, method, "--args", body, "--idempotency-key", f"meeting-{key}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"meroctl exit {r.returncode}").strip()[:500])
    return r.stdout.strip()
