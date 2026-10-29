$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Run python init_local.py first.' }
Write-Output 'Enable Google 2-Step Verification, then generate an app password at https://myaccount.google.com/apppasswords for satviksaini7777@gmail.com.'
$secret = Read-Host 'Gmail app password (hidden; do not enter your normal password)' -AsSecureString
$secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer).Replace(' ', '')
    if ($password -notmatch '^[a-zA-Z]{16}$') { throw 'Expected a 16-letter Gmail app password. No setting was changed.' }
    $content = [IO.File]::ReadAllText($envFile)
    $content = [regex]::Replace($content, '(?m)^GMAIL_APP_PASSWORD=.*$', ('GMAIL_APP_PASSWORD=' + $password))
    [IO.File]::WriteAllText($envFile, $content, [Text.UTF8Encoding]::new($false))
    Write-Output 'Saved the app password locally. Run python jobbot.py check-mail to authenticate without sending an email.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    $password = $null
}
