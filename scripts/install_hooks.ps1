<#
.SYNOPSIS
    Install the Claude Code token-tracking hooks.

.DESCRIPTION
    Registers this project's collector on Claude Code's Stop and SessionEnd
    events in the user-level settings file (~/.claude/settings.json), so every
    Claude Code session in every project is tracked with no per-project setup.

    The settings file is backed up before any change, existing hooks and all
    other settings are preserved, and running this script repeatedly is safe -
    it never creates duplicate hooks.

.PARAMETER SettingsPath
    Settings file to modify. Defaults to ~/.claude/settings.json.

.PARAMETER Python
    Python interpreter to bake into the hook command. Defaults to the project
    virtualenv if one exists, otherwise the interpreter running the install.

.PARAMETER Backfill
    Also import the Claude Code session history already on this machine.

.EXAMPLE
    .\scripts\install_hooks.ps1

.EXAMPLE
    .\scripts\install_hooks.ps1 -Backfill
#>
[CmdletBinding()]
param(
    [string] $SettingsPath,
    [string] $Python,
    [switch] $Backfill
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Resolve-Python {
    param([string] $Explicit)

    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "Python not found at: $Explicit" }
        return $Explicit
    }
    foreach ($candidate in @(
            (Join-Path $root '.venv\Scripts\python.exe'),
            (Join-Path $root 'venv\Scripts\python.exe'))) {
        if (Test-Path $candidate) { return $candidate }
    }
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $found) { $found = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($null -eq $found) {
        throw 'Python was not found on PATH. Install Python 3.9+ or pass -Python <path>.'
    }
    return $found.Source
}

$python = Resolve-Python -Explicit $Python

Write-Host ''
Write-Host 'Claude Code Token Usage Tracker - hook installation' -ForegroundColor Cyan
Write-Host ''
Write-Host ("  project : {0}" -f $root)
Write-Host ("  python  : {0}" -f $python)

# All settings-file surgery lives in tracker/hooks.py: one implementation, and
# it is covered by the test suite.
$installArgs = @('-m', 'tracker.hooks', 'install')
if ($SettingsPath) { $installArgs += @('--settings', $SettingsPath) }

Push-Location $root
try {
    & $python @installArgs
    if ($LASTEXITCODE -ne 0) { throw "Hook installation failed (exit $LASTEXITCODE)." }

    if ($Backfill) {
        Write-Host ''
        Write-Host 'Importing existing Claude Code history...' -ForegroundColor Cyan
        & $python -m tracker.cli backfill
        if ($LASTEXITCODE -ne 0) { throw "Backfill failed (exit $LASTEXITCODE)." }
    }

    Write-Host ''
    & $python -m tracker.cli status
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Done. Tracking starts with the next Claude Code session.' -ForegroundColor Green
Write-Host 'Start the dashboard with:  .\start_dashboard.bat'
Write-Host ''
