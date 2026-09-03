<#
.SYNOPSIS
    Install the Claude Code token-tracking hooks on Windows.

.DESCRIPTION
    Named for symmetry with install_linux.sh and install_macos.sh. It forwards
    every argument to scripts\install_hooks.ps1, which in turn runs the
    cross-platform Python installer - so all three platforms end up executing
    exactly the same code.

.EXAMPLE
    .\scripts\install_windows.ps1

.EXAMPLE
    .\scripts\install_windows.ps1 -Backfill
#>
[CmdletBinding()]
param(
    [string] $SettingsPath,
    [string] $Python,
    [switch] $Backfill
)

$ErrorActionPreference = 'Stop'

$forwarded = @{}
if ($SettingsPath) { $forwarded['SettingsPath'] = $SettingsPath }
if ($Python)       { $forwarded['Python'] = $Python }
if ($Backfill)     { $forwarded['Backfill'] = $true }

& (Join-Path $PSScriptRoot 'install_hooks.ps1') @forwarded
exit $LASTEXITCODE
