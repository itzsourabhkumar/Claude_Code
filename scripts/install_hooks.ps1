<#
.SYNOPSIS
    Install the Claude Code token-tracking hooks on Windows.

.DESCRIPTION
    A convenience wrapper. It finds an interpreter - the project virtualenv if
    one exists, otherwise Python on PATH - and runs scripts/install_hooks.py,
    which is the real, cross-platform implementation and the one covered by the
    test suite. Nothing about the install logic lives in this file.

    The Python installer registers this project's collector on Claude Code's
    Stop and SessionEnd events in the user-level settings file
    (~/.claude/settings.json), backs that file up first, preserves every
    unrelated hook and setting, and never creates a duplicate hook.

.PARAMETER SettingsPath
    Settings file to modify. Defaults to Claude Code's own.

.PARAMETER Python
    Python interpreter to bake into the hook command.

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
        throw 'Python was not found on PATH. Install Python 3.11+ from python.org or the Microsoft Store, or pass -Python <path>.'
    }
    return $found.Source
}

$python = Resolve-Python -Explicit $Python

$installArgs = @((Join-Path $root 'scripts\install_hooks.py'))
if ($SettingsPath) { $installArgs += @('--settings', $SettingsPath) }
if ($Python)       { $installArgs += @('--python', $Python) }
if ($Backfill)     { $installArgs += '--backfill' }

Push-Location $root
try {
    & $python @installArgs
    if ($LASTEXITCODE -ne 0) { throw "Hook installation failed (exit $LASTEXITCODE)." }

    Write-Host ''
    & $python -m tracker.cli status
}
finally {
    Pop-Location
}
