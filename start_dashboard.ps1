<#
.SYNOPSIS
    Start the Claude Code token usage dashboard on http://127.0.0.1:8765.

.DESCRIPTION
    A convenience wrapper. It uses the project virtualenv when one exists,
    otherwise whatever Python is on PATH, and runs server.py - the real
    implementation, identical on Windows, Linux and macOS. Every argument is
    passed straight through.

    The server binds to loopback only and has no dependencies to install.

.EXAMPLE
    .\start_dashboard.ps1

.EXAMPLE
    .\start_dashboard.ps1 --port 9000

.EXAMPLE
    .\start_dashboard.ps1 --no-browser
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ServerArgs
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$python = $null
foreach ($candidate in @(
        (Join-Path $root '.venv\Scripts\python.exe'),
        (Join-Path $root 'venv\Scripts\python.exe'))) {
    if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $found) { $found = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($null -eq $found) {
        Write-Host ''
        Write-Host '  Python was not found.' -ForegroundColor Red
        Write-Host '  Install Python 3.11+ from python.org or the Microsoft Store,'
        Write-Host '  or create a virtualenv in .venv, then try again.'
        Write-Host ''
        exit 1
    }
    $python = $found.Source
}

Write-Host ''
Write-Host '  Starting Claude Code Token Usage dashboard...' -ForegroundColor Cyan
Write-Host ''

Push-Location $root
try {
    if ($ServerArgs) { & $python 'server.py' @ServerArgs }
    else             { & $python 'server.py' }
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -ne 0) {
    Write-Host ''
    Write-Host ("  The dashboard exited with code {0}. See logs\server.log for details." -f $code)
    Write-Host ''
}
exit $code
