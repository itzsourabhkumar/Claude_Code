<#
.SYNOPSIS
    Remove the Claude Code token-tracking hooks.

.DESCRIPTION
    Removes only the hook entries this project installed - identified by their
    --cctracker marker. Any other hooks you have configured, and every other
    setting in the file, are left exactly as they were. The settings file is
    backed up before the change.

    Usage data already collected under data/ is NOT deleted; remove that folder
    yourself if you also want the history gone.

.PARAMETER SettingsPath
    Settings file to modify. Defaults to ~/.claude/settings.json.

.PARAMETER Python
    Python interpreter to run the uninstall with.

.EXAMPLE
    .\scripts\uninstall_hooks.ps1
#>
[CmdletBinding()]
param(
    [string] $SettingsPath,
    [string] $Python
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
    if ($null -eq $found) { throw 'Python was not found on PATH. Pass -Python <path>.' }
    return $found.Source
}

$python = Resolve-Python -Explicit $Python

Write-Host ''
Write-Host 'Claude Code Token Usage Tracker - hook removal' -ForegroundColor Cyan
Write-Host ''

$uninstallArgs = @('-m', 'tracker.hooks', 'uninstall')
if ($SettingsPath) { $uninstallArgs += @('--settings', $SettingsPath) }

Push-Location $root
try {
    & $python @uninstallArgs
    if ($LASTEXITCODE -ne 0) { throw "Hook removal failed (exit $LASTEXITCODE)." }
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Hooks removed. Collected usage data under data\ was left untouched.' -ForegroundColor Green
Write-Host ''
