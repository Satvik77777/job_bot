$ErrorActionPreference = 'Stop'
Stop-ScheduledTask -TaskName 'SatvikJobApplications' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'SatvikJobApplications' -Confirm:$false
Write-Output 'Removed the SatvikJobApplications task. Files and application history are preserved.'
