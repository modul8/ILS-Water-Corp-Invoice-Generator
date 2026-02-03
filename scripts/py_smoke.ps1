param(
  [string]$Python = "$PSScriptRoot\..\venv\Scripts\python.exe"
)

if (!(Test-Path $Python)) {
  Write-Error "Python not found at $Python"
  exit 1
}

$paths = @(
  "app.py",
  "ui\*.py",
  "services\*.py",
  "data\*.py"
)

foreach ($p in $paths) {
  & $Python -m py_compile $p
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

Write-Host "py_smoke: OK"
