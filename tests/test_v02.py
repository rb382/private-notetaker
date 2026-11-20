import json, os, stat, sys, tempfile, types, wave
from pathlib import Path
import numpy as np

out = tempfile.mkdtemp(prefix="nt02_")
os.environ["NOTETAKER_NOTES_DIR"] = out
sys.path.insert(0, sys.argv[1])
from notetaker import cli, pipeline, calimero, config

def make_wav(p, secs=4, rate=44100):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        t = np.arange(int(rate*secs))/rate
        w.writeframes((np.sin(2*np.pi*300*t)*6000).astype("<i2").tobytes())

src = Path(tempfile.mkdtemp())/"clip.wav"; make_wav(src)

# 1) CLI process with fake whisper: original kept, outputs + timing + meeting.json
def fake_tx(path, model, lang, progress=lambda p: None):
    assert Path(path) != src and Path(path).exists()
    progress(1.0); return [{"start":0.0,"end":2.0,"text":"Hello team."}], {"language":"en","duration":4.0}
real_tx = pipeline.transcribe
pipeline.transcribe = fake_tx
rc = cli.main(["process", str(src), "--no-notes", "--title", "Clip test"])
assert rc == 0 and src.exists(), "original must be kept"
d = sorted(Path(out).iterdir())[-1]
names = sorted(p.name for p in d.iterdir())
assert names == ["meeting.json","receipt.json","transcript.json","transcript.md"], names
r = json.loads((d/"receipt.json").read_text())
assert r["timing"]["audio_seconds"] == 4.0 and r["length_seconds"] == 4.0 and r["audio_deleted_at"]
assert r["steps"][-1]["what"] == "Notes skipped by request." or "Notes skipped" in json.dumps(r["steps"])
m = json.loads((d/"meeting.json").read_text())
assert m["schema"] == "notetaker.meeting/1" and m["transcript"][0] == {"start":0.0,"end":2.0,"speaker":None,"text":"Hello team."}
assert m["notes_md"] is None and m["receipt"]["audio_sha256"]
assert not list(Path(tempfile.gettempdir()).glob("notetaker_*/input.wav")), "temp copy must be gone"
assert cli.main(["process", str(src), "--model", "nope"]) == 2

# 2) Calimero push via a fake meroctl on PATH
bindir = Path(tempfile.mkdtemp()); log = bindir/"calls.json"
fake = bindir/"meroctl"
fake.write_text(f"#!{sys.executable}\nimport json,sys\nopen({str(log)!r},'w').write(json.dumps(sys.argv[1:]))\nprint('ok')\n")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]
cal = {"enabled": True, "mode": "meroctl", "meroctl": "meroctl", "node": "node1", "instance": "INST",
       "participants": ["bob", ""], "method": "addMeeting"}
assert calimero.push(m, cal) == "ok"
args = json.loads(log.read_text())
assert args[:6] == ["--node","node1","call","--instance","INST","addMeeting"], args
assert args[6] == "--args" and args[8:] == ["--idempotency-key", f"meeting-{m['id']}"], args
sent = json.loads(args[7])
assert sent["participants"] == ["bob"], sent
mt = sent["meeting"]
assert mt["id"] == m["id"] and isinstance(mt["lengthSeconds"], float) and mt["notesMd"] == ""
assert set(mt) == {"id","title","startedAt","endedAt","lengthSeconds","language","transcript","notesMd","receiptJson"}
assert json.loads(mt["receiptJson"])["audio_sent_over_network"] is False
try: calimero.push(m, {**cal, "instance": ""}); raise SystemExit("should fail")
except RuntimeError as e: assert "instance" in str(e)
# Too long for a command line: refused with a pointer to proxy mode.
big = {**m, "transcript": [{"start": 0, "end": 1, "text": "x" * 200_000}]}
try: calimero.push(big, cal); raise SystemExit("should fail")
except RuntimeError as e: assert "proxy" in str(e)

# 2b) Proxy mode: a local stand-in for `meroctl proxy` receives the intent.
import http.server, threading
got = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        got["path"] = self.path
        got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(b'{"result":null}')
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
pcal = {"enabled": True, "mode": "proxy", "proxy_url": f"http://127.0.0.1:{srv.server_port}", "instance": "app123", "method": "add_meeting"}
assert calimero.push(big, pcal) == '{"result":null}'
assert got["path"] == "/apps/app123/intents", got
assert got["body"]["method"] == "addMeeting" and got["body"]["idempotencyKey"] == f"meeting-{m['id']}"
assert got["body"]["args"]["meeting"]["transcript"][0]["text"] == "x" * 200_000
srv.shutdown()
try: calimero.push(m, {**pcal, "proxy_url": "http://127.0.0.1:9"}); raise SystemExit("should fail")
except RuntimeError as e: assert "meroctl proxy" in str(e)

# 3) Pipeline with calimero enabled records the push in the receipt
cfg = config.load(); cfg["calimero"] = cal; cfg["write_notes"] = False
d2 = Path(out)/"pushed"; d2.mkdir()
a2 = Path(tempfile.mkdtemp())/"a.wav"; make_wav(a2)
pipeline.run(a2, d2, {"title":"Pushed","started_at":"x","ended_at":"y","seconds":4.0,"consent":{}}, cfg, {})
steps = [s["what"] for s in json.loads((d2/"receipt.json").read_text())["steps"]]
assert "Stored in Calimero." in steps, steps
assert not a2.exists()

# 4) Parakeet path with a stand-in onnx_asr (real audio decoding)
pipeline.transcribe = real_tx
class Seg:
    def __init__(s, a, b, t): s.start, s.end, s.text = a, b, t
class FakeModel:
    def recognize(self, audio, sample_rate=16000):
        assert sample_rate == 16000 and audio.dtype == np.float32 and abs(len(audio)/16000 - 4.0) < 0.05
        yield Seg(0.1, 1.9, " Good morning. "); yield Seg(2.0, 3.9, "")
class FakeAsr:
    def with_vad(self, vad): return FakeModel()
fake_mod = types.SimpleNamespace(load_model=lambda name, quantization=None: FakeAsr(), load_vad=lambda name: object())
sys.modules["onnx_asr"] = fake_mod
segs, info = pipeline.transcribe(src, "parakeet-v3", "en")
assert segs == [{"start":0.1,"end":1.9,"text":"Good morning."}] and abs(info["duration"]-4.0) < 0.05, (segs, info)
print("V0.2 TESTS PASSED")
