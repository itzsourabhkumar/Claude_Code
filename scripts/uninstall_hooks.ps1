<#
.SYNOPSIS
    Remove the Claude Code token-tracking hooks on Windows.

.DESCRIPTION
    A convenience wrapper around scripts/uninstall_hooks.py, which is the real,
    cross-platform implementation.

    Only the hook entries this project installed - identified by their
    --cctracker marker - are removed. Any other hooks you have configured, and
    every other setting in the file, are left exactly as they were, and the
    settings file is backed up before the change.

    Usage data already collected under data\ is NOT deleted unless -PurgeData is
    given.

.PARAMETER SettingsPath
    Settings file to modify. Defaults to Claude Code's own.

.PARAMETER Python
    Python interpreter to run the uninstall with.

.PARAMETER PurgeData
    Also delete the collected usage data, reports and logs (asks first).

.EXAMPLE
    .\scripts\uninstall_hooks.ps1

.EXAMPLE
    .\scripts\uninstall_hooks.ps1 -PurgeData
#>
[CmdletBinding()]
param(
    [string] $SettingsPath,
    [string] $Python,
    [switch] $PurgeData
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

$uninstallArgs = @((Join-Path $root 'scripts\uninstall_hooks.py'))
if ($SettingsPath) { $uninstallArgs += @('--settings', $SettingsPath) }
if ($PurgeData)    { $uninstallArgs += '--purge-data' }

Push-Location $root
try {
    & $python @uninstallArgs
    if ($LASTEXITCODE -ne 0) { throw "Hook removal failed (exit $LASTEXITCODE)." }
}
finally {
    Pop-Location
}
