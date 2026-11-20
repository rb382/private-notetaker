"""Local web app on http://127.0.0.1:8765. It only listens on this computer."""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__, config, pipeline
from .recorder import SD_ERROR, Recorder, list_inputs

STATIC = Path(__file__).resolve().parent / "static"
cfg = config.load()
rec = Recorder()
status: dict = {"state": "idle", "message": "", "progress": None, "meeting": None}
current: dict = {}

app = FastAPI(title="Private Notetaker", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Refuse requests from other websites in the browser (DNS rebinding, cross-site POSTs).
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return JSONResponse({"detail": "Local use only."}, status_code=403)
    if request.method != "GET" and request.headers.get("x-notetaker") != "1":
        return JSONResponse({"detail": "Missing app header."}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
def info():
    return {
        "version": __version__,
        "devices": list_inputs(),
        "audio_error": SD_ERROR,
        "settings": {k: cfg[k] for k in ("speech_model", "language", "notes_model", "notes_dir")},
        "speech_models": config.SPEECH_MODELS,
        "ollama": pipeline.ollama_available(cfg["ollama_url"]),
    }


class Settings(BaseModel):
    speech_model: str | None = None
    language: str | None = None
    notes_model: str | None = None


@app.post("/api/settings")
def set_settings(s: Settings):
    if s.speech_model:
        if s.speech_model not in config.SPEECH_MODELS:
            raise HTTPException(400, "Unknown speech model.")
        cfg["speech_model"] = s.speech_model
    if s.language:
        cfg["language"] = s.language
    if s.notes_model:
        cfg["notes_model"] = s.notes_model.strip()
    config.save(cfg)
    return {"ok": True}


class Start(BaseModel):
    title: str = ""
    device: int | None = None
    consent: bool = False
    people: int | None = None


@app.post("/api/start")
def start(body: Start):
    if status["state"] in ("recording", "processing"):
        raise HTTPException(409, "Already busy. Stop or wait for the current meeting first.")
    if not body.consent:
        raise HTTPException(400, "Everyone present must agree before recording starts.")
    now = dt.datetime.now().astimezone()
    title = body.title.strip() or "Meeting"
    folder = config.notes_dir(cfg) / f"{now:%Y-%m-%d_%H%M}_{pipeline.slug(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="notetaker_", suffix=".wav")
    os.close(fd)
    try:
        rec.start(body.device, Path(tmp))
    except Exception as exc:
        Path(tmp).unlink(missing_ok=True)
        folder.rmdir()
        raise HTTPException(500, f"Could not open the microphone: {exc}")
    current.clear()
    current.update({
        "folder": folder, "audio": Path(tmp), "title": title,
        "started_at": now.isoformat(timespec="seconds"),
        "consent": {"confirmed_by_host": True, "people_present": body.people,
                    "terms": ["Written down word for word, and summarised.",
                              "Audio deleted as soon as that is done.",
                              "Only the people in this meeting get the transcript and notes.",
                              "Anyone can say no, or stop it at any time."],
                    "at": now.isoformat(timespec="seconds")},
    })
    status.update({"state": "recording", "message": "Listening.", "progress": None, "meeting": folder.name})
    return {"ok": True, "meeting": folder.name}


def _process(meta: dict, audio: Path, folder: Path):
    try:
        pipeline.run(audio, folder, meta, cfg, status)
        status.update({"state": "done", "progress": None})
    except Exception as exc:
        status.update({"state": "error", "message": f"Something went wrong: {exc}", "progress": None})
    finally:
        if audio.exists():
            pipeline.delete_audio(audio)


@app.post("/api/stop")
def stop(discard: bool = False):
    if status["state"] != "recording":
        raise HTTPException(409, "Not recording.")
    seconds = rec.stop()
    audio, folder = current["audio"], current["folder"]
    if discard:
        pipeline.delete_audio(audio)
        try:
            folder.rmdir()
        except OSError:
            pass
        status.update({"state": "idle", "message": "Stopped. Nothing was kept.", "progress": None, "meeting": None})
        return {"ok": True, "discarded": True}
    meta = {"title": current["title"], "started_at": current["started_at"],
            "ended_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "seconds": seconds, "consent": current["consent"]}
    status.update({"state": "processing", "message": "Preparing.", "progress": 0.0})
    threading.Thread(target=_process, args=(meta, audio, folder), daemon=True).start()
    return {"ok": True}


@app.get("/api/status")
def get_status():
    return {**status, "elapsed": round(rec.elapsed(), 1), "level": round(rec.level, 3), "audio_warning": rec.error}


def _meeting_dir(name: str) -> Path:
    base = config.notes_dir(cfg).resolve()
    p = (base / name).resolve()
    if p.parent != base or not p.is_dir():
        raise HTTPException(404, "Meeting not found.")
    return p


@app.get("/api/meetings")
def meetings():
    base = config.notes_dir(cfg)
    out = []
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        r = d / "receipt.json"
        title = d.name
        when = ""
        if r.exists():
            try:
                data = json.loads(r.read_text(encoding="utf-8"))
                title, when = data.get("title", title), data.get("started_at", "")
            except ValueError:
                pass
        out.append({"id": d.name, "title": title, "started_at": when,
                    "has_notes": (d / "notes.md").exists(), "has_transcript": (d / "transcript.md").exists()})
    return out


@app.get("/api/meetings/{name}")
def meeting(name: str):
    d = _meeting_dir(name)
    read = lambda f: (d / f).read_text(encoding="utf-8") if (d / f).exists() else None
    receipt = read("receipt.json")
    return {"id": name, "notes": read("notes.md"), "transcript": read("transcript.md"),
            "receipt": json.loads(receipt) if receipt else None}


@app.post("/api/meetings/{name}/open")
def open_folder(name: str):
    d = _meeting_dir(name)
    if sys.platform.startswith("win"):
        os.startfile(d)  # noqa: S606 - opens Explorer on the user's own folder
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    else:
        subprocess.Popen(["xdg-open", str(d)])
    return {"ok": True}


def main():
    import uvicorn
    import webbrowser
    port = int(cfg.get("port", 8765))
    url = f"http://127.0.0.1:{port}/"
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Private Notetaker {__version__} is running at {url}")
    print("Close this window to quit.")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
