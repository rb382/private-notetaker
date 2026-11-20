# Private Notetaker 0.2

A first test version. It records a meeting from a microphone, writes a word-for-word
transcript and summary notes **on this computer**, then deletes the audio.
Nothing is sent to the internet. There is no account.

Runs on Windows, macOS, Linux and ChromeOS (in its Linux environment).
Engineers and Calimero: see HANDOVER.md.

## Install on macOS, Linux or ChromeOS

ChromeOS: first turn on Linux (Settings > About ChromeOS > Developers > Linux development environment),
allow Linux to use the microphone, then in the Linux terminal run
`sudo apt install python3-venv libportaudio2`.

Then, in this folder: `./setup.sh` and start the app with `./start.sh`.
Add `--parakeet` to setup for the optional Parakeet speech model.

## Install on Windows (once, about 10 minutes)

1. Install **Python 3.12** from https://www.python.org/downloads/windows/
   During install, tick **"Add python.exe to PATH"**.
2. Get the code: `git clone <repo URL> C:\PrivateNotetaker`, or on GitHub choose **Code → Download ZIP**, then right-click the zip, choose **Properties**, tick **Unblock** if shown, and unzip it to `C:\PrivateNotetaker`.
3. Right-click **setup.ps1** and choose **Run with PowerShell**.
   It installs what the app needs and downloads the speech model (about 500 MB).
   If Windows asks whether to allow it, choose Yes / Run anyway.
4. Optional, for summary notes: install **Ollama** from https://ollama.com,
   then open a terminal and run `ollama pull llama3.2:3b`.
   Without it you still get the full transcript.

## Use

1. Double-click **Private Notetaker** on your desktop. A small window opens and the app opens in your browser.
2. Read the four lines out to the room, tick the box, choose the microphone and press **Start listening**.
3. Press **Stop and write notes** when done, or **Stop and keep nothing** to throw it away.
4. After a few minutes the notes, transcript and receipt appear. They are saved in
   `C:\Users\<you>\PrivateNotes\`.

Close the small black window to quit the app.

## What happens to the audio

- It is recorded to a temporary file on this computer.
- It is transcribed here, by the speech model on this computer.
- The file is then overwritten with zeros and deleted. The receipt records when.
- The app only accepts connections from this computer (127.0.0.1).

For stronger protection of deleted files, turn on BitLocker (Settings > Privacy & security > Device encryption).

## Speed and accuracy

On a typical laptop without a graphics card, `small.en` takes roughly a third to half of the meeting's
length to transcribe. Change the model under **Settings** in the app:
- `base.en`: fastest, less accurate
- `small.en`: default
- `large-v3-turbo`: most accurate, much slower on a laptop
Models ending in `.en` are English only.

## What to report back

After each test, tell Claude:
1. Did setup finish? If not, paste the red text.
2. Which microphone you used, and how many people were in the room.
3. How long the meeting was and how long the transcript took.
4. How accurate the transcript and notes were, with one or two examples of mistakes.

## Not in this version yet

- The one-tap phone consent link for guests (the host confirms consent in this version)
- Speaker labels (who said what)
- Sharing with participants through Calimero
- Processing in a verified enclave
