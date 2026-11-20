# Starts Private Notetaker and opens it in your browser. Close this window to quit.
Set-Location -Path $PSScriptRoot
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Run setup.ps1 first." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}
& $venvPy -m notetaker
if ($LASTEXITCODE -ne 0) { Read-Host "The app stopped with an error. Copy the text above and send it to Claude, then press Enter" }
