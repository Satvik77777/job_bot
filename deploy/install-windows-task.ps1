$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runner = Join-Path $PSScriptRoot 'run-jobbot.ps1'
$pythonLauncher = (Get-Command python.exe -ErrorAction Stop).Source
$pythonCommand = (& $pythonLauncher -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonCommand)) { throw 'Cannot resolve the actual Python interpreter.' }
& $pythonCommand (Join-Path $taskRoot 'jobbot.py') health
if ($LASTEXITCODE -ne 0) { throw 'Fix configuration before installing the scheduled task.' }
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "' + $runner + '" -PythonExecutable "' + $pythonCommand + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $taskRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'SatvikJobApplications' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Discover every two hours; dispatch verified applications with persistent pacing.' -Force | Out-Null
Write-Output 'Installed SatvikJobApplications. Runs only while this user is logged in and the PC is powered on. SMTP remains governed by .env.'
Get-ScheduledTask -TaskName 'SatvikJobApplications' | Select-Object TaskName,State
