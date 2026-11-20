# Private Notetaker 0.2: handover for Calimero

From Rune Bentien (Lyrik). Prepared 20 November 2026.

## What this is

A first working version of a privacy-first meeting notetaker. It follows five rules:

1. Nothing is captured until everyone present has agreed.
2. Audio is processed on the user's device, or in a verified enclave, never on an ordinary server.
3. Audio is deleted as soon as the transcript is written. Only the transcript and notes are kept.
4. Only the people in the meeting get the transcript and notes.
5. Every meeting gets a receipt saying what ran, where, and when the audio was deleted.

Today it runs entirely on one computer. The next step is to store and share each meeting
through Calimero, and to offer transcription on your attested job nodes. That is what we'd like
your help with.

## What's in the repo

| Path | What it is |
|---|---|
| `notetaker/app.py` | Local web app on http://127.0.0.1:8765 (listens on this machine only) |
| `notetaker/recorder.py` | Microphone capture to a temporary WAV |
| `notetaker/pipeline.py` | Transcribe, write notes, delete audio, write the receipt |
| `notetaker/calimero.py` | Builds the meeting payload and pushes it to a context with `meroctl` |
| `notetaker/cli.py` | Command line: process a file without a microphone, or push a meeting |
| `setup.sh` / `start.sh` | Setup and start on macOS, Linux and ChromeOS |
| `setup.ps1` / `start.ps1` | The same for Windows |
| `tests/` | Tests with stand-in microphone and speech models (see `tests/README.md`) |

Speech engines: faster-whisper (Whisper `base.en`, `small.en`, `medium.en`, `large-v3-turbo`, `large-v3`)
and, optionally, NVIDIA Parakeet TDT 0.6B v3 through onnx-asr. Notes are written by a local Ollama model
if one is running; otherwise you get the transcript only.

## Quick start (macOS or Linux)

```bash
git clone <repo URL> notetaker && cd notetaker
./setup.sh --parakeet        # Python 3.10-3.13. Linux also needs: sudo apt install libportaudio2
./start.sh                   # opens the app in a browser
```

Models download from Hugging Face on first use (`small.en` is about 500 MB, Parakeet about 650 MB).

## Test 1: transcription speed on your CPU job nodes

The same pipeline runs from the command line, with no microphone. It works on a copy of the file,
so the original is never deleted.

```bash
.venv/bin/python -m notetaker.cli process clip.wav --model small.en    --no-notes --out ./out
.venv/bin/python -m notetaker.cli process clip.wav --model parakeet-v3 --no-notes --out ./out
```

Each run prints a JSON summary. Please send back the `timing` block for each model,
plus the CPU type and core count. `seconds_per_audio_minute` is the number we care about.
I can provide a two-minute English meeting clip; any wav, mp3 or m4a works.

If the job nodes can't reach Hugging Face, run once on a machine that can and copy the model
cache (`~/.cache/huggingface`) into the image.

## Test 2: storing a meeting in a Calimero context

Every finished meeting writes `meeting.json` to its folder. That object is the whole handover
between the app and Calimero. Schema `notetaker.meeting/1`:

```json
{
  "schema": "notetaker.meeting/1",
  "id": "2026-11-20_1000_Weekly-sync",
  "title": "Weekly sync",
  "started_at": "2026-11-20T10:00:00+01:00",
  "ended_at": "2026-11-20T10:32:10+01:00",
  "length_seconds": 1930.4,
  "language": "en",
  "transcript": [
    {"start": 0.0, "end": 4.2, "speaker": null, "text": "Let's start with the roadmap."}
  ],
  "notes_md": "## Key points\n- ...\n\n## Decisions\n- ...\n\n## Actions\n- ...",
  "receipt": {
    "processed_on": "this computer (hostname)",
    "audio_sent_over_network": false,
    "audio_sha256": "...",
    "speech_model": "small.en",
    "audio_deleted_at": "2026-11-20T10:35:02+01:00",
    "consent": {"confirmed_by_host": true, "people_present": 4, "terms": ["..."]},
    "timing": {"audio_seconds": 1930.4, "transcribe_seconds": 640.2, "seconds_per_audio_minute": 19.9},
    "steps": [{"at": "...", "what": "..."}]
  }
}
```

`speaker` is always `null` for now; speaker labels come later (we're looking at Trelis Tiron).

### What we'd like the Calimero app to expose

| Method | Args | Returns |
|---|---|---|
| `add_meeting` | `{"meeting": <notetaker.meeting/1>}` | the meeting `id` |
| `get_meeting` | `{"id": "..."}` | the meeting object |
| `list_meetings` | `{}` | `[{"id", "title", "started_at"}]` |
| `delete_meeting` | `{"id": "..."}` | `true` |

Our side already calls `add_meeting`. Switch it on in `config.json`:

```json
"calimero": {
  "enabled": true,
  "meroctl": "meroctl",
  "node": "node1",
  "context_id": "<CONTEXT_ID>",
  "member_key": "<MEMBER_PUBLIC_KEY>",
  "method": "add_meeting"
}
```

That runs `meroctl --node node1 call add_meeting --context <CONTEXT_ID> --as <MEMBER_PUBLIC_KEY> --args '{"meeting": {...}}'`.
To resend an existing meeting: `.venv/bin/python -m notetaker.cli push <meeting folder>`.
If you'd rather we use your Python or JS client, only `push()` in `notetaker/calimero.py` needs to change.

A one-hour transcript is roughly 60-100 KB of JSON. Tell us if that's too large for one call.

## What we're asking of Calimero

1. Run Test 1 on a CPU job node and share the timings.
2. Build the small Calimero app with the four methods above, or tell us what you'd change.
3. Run Test 2 between two nodes and confirm the meeting syncs.
4. Tell us how a guest could read one meeting through a link, in a browser, with no account.
5. Tell us what the signed receipt from a job node contains, so we can merge it into ours.
6. Tell us the timing for Calimero 1.0 and GPU inference, and whether GPU inference could serve
   a model like Trelis Tiron (Whisper-large based, Apache 2.0, does speaker labels).

## Known limits in 0.2

- Consent is confirmed by the host in the app. The one-tap guest link is designed but not built.
- No speaker labels yet.
- The audio file is overwritten and deleted, but SSDs may keep old copies internally; full-disk encryption covers that.
- `meeting.json` holds the full transcript. Treat it as personal data.
- Tested here with stand-in audio and models. The real speech models have not yet run end to end in our test environment.
