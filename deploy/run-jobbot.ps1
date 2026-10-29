param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $taskRoot
$logDirectory = Join-Path $taskRoot 'data/logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logFile = Join-Path $logDirectory ((Get-Date -Format 'yyyy-MM-dd') + '.log')
$pythonCommand = if ($PythonExecutable) { $PythonExecutable } else { (Get-Command python.exe -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $pythonCommand)) { throw 'Configured Python executable is missing.' }
$pythonDirectory = Split-Path -Parent $pythonCommand
$runtimeDirectories = @($pythonDirectory, (Join-Path $pythonDirectory 'DLLs'), (Join-Path $pythonDirectory 'Library/bin')) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
$env:PATH = ($runtimeDirectories -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + $env:PATH
& $pythonCommand (Join-Path $taskRoot 'jobbot.py') tick *>> $logFile
exit $LASTEXITCODE
