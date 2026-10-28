$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend"
if (!(Test-Path $python)) { throw "Create .venv and install requirements.txt first." }
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (!$npmCommand) {
    $packageRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $nodePackage = Get-ChildItem $packageRoot -Directory -Filter "OpenJS.NodeJS.LTS*" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $nodeDirectory = if ($nodePackage) {
        Get-ChildItem $nodePackage.FullName -Directory -Filter "node-v*" | Select-Object -First 1
    }
    if (!$nodeDirectory) { throw "Install Node.js LTS and ensure npm is on PATH." }
    $npmPath = Join-Path $nodeDirectory.FullName "npm.cmd"
    $env:PATH = "$($nodeDirectory.FullName);$env:PATH"
} else {
    $npmPath = $npmCommand.Source
}
Start-Process -FilePath $python -ArgumentList "-m uvicorn backend.app:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root
Start-Process -FilePath $npmPath -ArgumentList "run dev" -WorkingDirectory $frontend
Write-Host "Backend: http://localhost:8000  Frontend: http://localhost:5173"
