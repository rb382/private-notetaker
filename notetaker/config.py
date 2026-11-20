"""Settings and where files are kept. Everything stays on this computer."""
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = APP_DIR / "config.json"

DEFAULTS = {
    # Where transcripts and notes are saved. Kept outside Documents on purpose,
    # because Windows often syncs Documents to OneDrive.
    "notes_dir": str(Path.home() / "PrivateNotes"),
    # Speech model run by faster-whisper on this computer.
    # base.en is fastest, small.en is a good default, large-v3-turbo is most accurate but slow on CPU.
    "speech_model": "small.en",
    "language": "en",
    # Optional: a local Ollama model for writing notes. Leave as is if Ollama isn't installed.
    "ollama_url": "http://127.0.0.1:11434",
    "notes_model": "llama3.2:3b",
    "port": 8765,
    # Optional: store each finished meeting in an instance of the Calimero meetings app
    # (calimero-app/). See HANDOVER.md.
    "calimero": {
        "enabled": False,
        "instance": "",          # the app instance id (or a meroctl alias in "meroctl" mode)
        "participants": [],      # accounts that may read every meeting, besides you
        "mode": "proxy",         # "proxy" (via `meroctl proxy`) or "meroctl" (short meetings only)
        "proxy_url": "http://127.0.0.1:2580",
        "meroctl": "meroctl",
        "node": "",              # meroctl mode: a saved node alias or URL; empty = meroctl's default
        "method": "addMeeting",
    },
}

SPEECH_MODELS = ["base.en", "small.en", "medium.en", "large-v3-turbo", "small", "large-v3", "parakeet-v3"]
# parakeet-v3 = NVIDIA Parakeet TDT 0.6B v3 (25 European languages) via onnx-asr. Optional install.


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cal = saved.pop("calimero", None)
            cfg.update(saved)
            if isinstance(cal, dict):
                cfg["calimero"].update(cal)
        except (OSError, ValueError):
            pass
    env_dir = os.environ.get("NOTETAKER_NOTES_DIR")
    if env_dir:
        cfg["notes_dir"] = env_dir
    return cfg


def save(cfg: dict) -> None:
    keep = {k: cfg[k] for k in DEFAULTS if k in cfg}
    CONFIG_FILE.write_text(json.dumps(keep, indent=2), encoding="utf-8")


def notes_dir(cfg: dict) -> Path:
    p = Path(cfg["notes_dir"]).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
