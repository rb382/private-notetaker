"""Records from a chosen microphone to a temporary WAV file on this computer."""
import threading
import time
import wave
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except Exception as exc:  # PortAudio missing or no audio system
    sd = None
    SD_ERROR = str(exc)
else:
    SD_ERROR = ""


def list_inputs() -> list[dict]:
    if sd is None:
        return []
    out = []
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    hostapis = sd.query_hostapis()
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) < 1:
            continue
        api = hostapis[dev["hostapi"]]["name"] if dev.get("hostapi") is not None else ""
        # On Windows the same mic appears once per audio system; prefer the shared WASAPI/MME entries.
        out.append({
            "id": idx,
            "name": dev["name"],
            "api": api,
            "default": idx == default_in,
            "rate": int(dev.get("default_samplerate") or 16000),
        })
    out.sort(key=lambda d: (not d["default"], "WDM-KS" in d["api"], d["name"]))
    return out


class Recorder:
    def __init__(self) -> None:
        self._stream = None
        self._wav = None
        self._lock = threading.Lock()
        self.path: Path | None = None
        self.started_at: float | None = None
        self.level = 0.0
        self.frames = 0
        self.rate = 16000
        self.error = ""

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self, device_id: int | None, path: Path) -> None:
        if sd is None:
            raise RuntimeError(f"Audio is not available on this computer: {SD_ERROR}")
        if self.active:
            raise RuntimeError("Already recording.")
        info = sd.query_devices(device_id, "input") if device_id is not None else sd.query_devices(kind="input")
        self.rate = int(info.get("default_samplerate") or 16000)
        self.path = path
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.rate)
        self.frames = 0
        self.level = 0.0
        self.error = ""

        def callback(indata, frames, _time, status):
            if status:
                self.error = str(status)
            mono = indata.mean(axis=1) if indata.ndim > 1 and indata.shape[1] > 1 else indata.reshape(-1)
            self.level = float(min(1.0, np.sqrt(np.mean(mono ** 2)) * 6))
            pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            with self._lock:
                if self._wav is not None:
                    self._wav.writeframes(pcm)
                    self.frames += frames

        self._stream = sd.InputStream(device=device_id, channels=1, samplerate=self.rate,
                                      dtype="float32", callback=callback)
        self._stream.start()
        self.started_at = time.time()

    def stop(self) -> float:
        """Stops recording. Returns the length in seconds."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        with self._lock:
            if self._wav is not None:
                self._wav.close()
                self._wav = None
        self.level = 0.0
        return self.frames / self.rate if self.rate else 0.0

    def elapsed(self) -> float:
        return time.time() - self.started_at if self.active and self.started_at else 0.0
