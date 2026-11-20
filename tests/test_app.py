import json, os, sys, tempfile, time, wave
from pathlib import Path
import numpy as np

notes_dir = tempfile.mkdtemp(prefix="nt_notes_")
os.environ["NOTETAKER_NOTES_DIR"] = notes_dir
sys.path.insert(0, sys.argv[1])

from fastapi.testclient import TestClient
from notetaker import app as appmod, pipeline

# Fake microphone: writes 3 s of tone into the file it is given.
class FakeRec:
    def __init__(self): self.path=None; self.level=0.4; self.error=""; self._on=False
    def start(self, device, path):
        self.path=Path(path); self._on=True
        with wave.open(str(path),"wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            t=np.arange(16000*3)/16000; w.writeframes((np.sin(2*np.pi*440*t)*8000).astype("<i2").tobytes())
    def stop(self): self._on=False; return 3.0
    def elapsed(self): return 1.0 if self._on else 0.0
appmod.rec = FakeRec()

def fake_transcribe(path, model, lang, progress=lambda p: None):
    assert Path(path).exists(), "audio should exist while transcribing"
    progress(0.5); progress(1.0)
    return [{"start":0.0,"end":1.2,"text":"Let's agree to ship on Friday."},{"start":1.3,"end":2.8,"text":"Anna will send the invite."}], {"language":"en","duration":3.0}
pipeline.transcribe = fake_transcribe
calls = {}
def fake_chat(url, model, system, user, timeout=600):
    calls["user"] = user; return "## Key points\n- Shipping Friday\n\n## Decisions\n- Ship on Friday\n\n## Actions\n- Send the invite (Anna)"
pipeline._ollama_chat = fake_chat

c = TestClient(appmod.app, base_url="http://127.0.0.1:8765")
bad = TestClient(appmod.app, base_url="http://evil.example")
H = {"X-Notetaker":"1"}

assert bad.get("/api/info").status_code == 403, "non-local host must be refused"
assert c.post("/api/start", json={"consent":True}).status_code == 403, "POST without app header must be refused"
assert c.get("/").status_code == 200 and "Private Notetaker" in c.get("/").text
info = c.get("/api/info").json(); assert "speech_models" in info

r = c.post("/api/start", json={"title":"Weekly sync","consent":False}, headers=H)
assert r.status_code == 400, r.text

def run_meeting(title, ollama):
    pipeline.ollama_available = lambda url: ollama
    r = c.post("/api/start", json={"title":title,"device":0,"consent":True,"people":3}, headers=H); assert r.status_code == 200, r.text
    audio = appmod.current["audio"]; assert audio.exists()
    assert c.post("/api/start", json={"consent":True}, headers=H).status_code == 409
    assert c.get("/api/status").json()["state"] == "recording"
    assert c.post("/api/stop", headers=H).status_code == 200
    for _ in range(100):
        s = c.get("/api/status").json()
        if s["state"] in ("done","error"): break
        time.sleep(0.05)
    assert s["state"] == "done", s
    assert not audio.exists(), "audio must be deleted"
    return Path(notes_dir) / s["meeting"]

d = run_meeting("Weekly sync", ollama=True)
files = sorted(p.name for p in d.iterdir())
assert files == ["meeting.json","notes.md","receipt.json","transcript.json","transcript.md"], files
rc = json.loads((d/"receipt.json").read_text())
assert rc["audio_deleted_at"] and rc["audio_sha256"] and rc["audio_sent_over_network"] is False
assert rc["consent"]["people_present"] == 3 and rc["notes_model"]
assert "[00:00] Let's agree" in (d/"transcript.md").read_text()
assert "Anna will send" in calls["user"]

d2 = run_meeting("No ollama", ollama=False)
assert not (d2/"notes.md").exists() and (d2/"transcript.md").exists()
assert "Ollama is not running" in json.loads((d2/"receipt.json").read_text())["steps"][-1]["what"]

# discard keeps nothing
pipeline.ollama_available = lambda url: True
c.post("/api/start", json={"title":"Throwaway","consent":True}, headers=H)
a = appmod.current["audio"]; f = appmod.current["folder"]
r = c.post("/api/stop?discard=true", headers=H).json(); assert r["discarded"]
assert not a.exists() and not f.exists()

ms = c.get("/api/meetings").json(); assert len(ms) == 2, ms
m = c.get("/api/meetings/"+ms[0]["id"]).json(); assert m["transcript"] and m["receipt"]
assert c.get("/api/meetings/..%2F..%2Fetc").status_code in (404, 400)
assert c.get("/api/meetings/nope").status_code == 404

# long transcript chunking
n = {"k":0}
def counting_chat(url, model, system, user, timeout=600): n["k"]+=1; return "## Key points\n- x"
pipeline._ollama_chat = counting_chat
pipeline.write_notes("line of speech text\n"*4000, "u", "m"); assert n["k"] >= 3, n

# delete_audio really removes
p = Path(tempfile.mktemp()); p.write_bytes(b"secret"*1000); pipeline.delete_audio(p); assert not p.exists()
print("ALL TESTS PASSED")
