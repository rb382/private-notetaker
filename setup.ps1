# Private Notetaker - one-time setup for Windows.
# Right-click this file and choose "Run with PowerShell", or run:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
Write-Host "Setting up Private Notetaker..." -ForegroundColor Green

# Find Python 3.10-3.12 (best supported by the speech engine), then any Python 3.
$py = $null
foreach ($v in @("3.12", "3.11", "3.10")) {
    try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
}
if (-not $py) {
    try { & py -3 -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-3") } } catch {}
}
if (-not $py) {
    Write-Host ""
    Write-Host "Python was not found." -ForegroundColor Red
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/windows/"
    Write-Host "During install, tick 'Add python.exe to PATH'. Then run this setup again."
    Read-Host "Press Enter to close"
    exit 1
}
Write-Host "Using Python: $($py -join ' ')"

if (-not (Test-Path ".venv")) {
    & $py[0] $py[1] -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
# pip and the model download print progress to stderr; don't treat that as failure.
$ErrorActionPreference = "Continue"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing packages failed. Copy the red text above and send it to Claude." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host ""
Write-Host "Downloading the speech model (about 500 MB, once)..." -ForegroundColor Green
& $venvPy -c "from faster_whisper import WhisperModel; WhisperModel('small.en', device='cpu', compute_type='int8'); print('Speech model ready.')"

# Desktop shortcut to start the app.
try {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut([Environment]::GetFolderPath("Desktop") + "\Private Notetaker.lnk")
    $lnk.TargetPath = "powershell.exe"
    $lnk.Arguments = "-ExecutionPolicy Bypass -NoProfile -File `"$PSScriptRoot\start.ps1`""
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.Save()
    Write-Host "Added a 'Private Notetaker' shortcut to your desktop."
} catch {
    Write-Host "Could not add a desktop shortcut. Start the app with start.ps1 instead."
}

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
Write-Host "Optional: install Ollama from https://ollama.com and run 'ollama pull llama3.2:3b' to get summary notes."
Read-Host "Press Enter to close"
