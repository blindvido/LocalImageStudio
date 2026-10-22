$ErrorActionPreference = 'Stop'
$taskStudioRoot = $PSScriptRoot
$taskPython = Join-Path (Split-Path $taskStudioRoot -Parent) 'python_embeded\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'ComfyUI portable Python was not found.' }
& $taskPython -X utf8 (Join-Path $taskStudioRoot 'agent_cli.py') start
if ($LASTEXITCODE -ne 0) { throw 'Studio failed to start. Check local-image-studio/data/logs/studio.log.' }
Start-Process 'http://127.0.0.1:8190'
