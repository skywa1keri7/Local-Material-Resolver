param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Asset,

    [string]$Output,
    [ValidateSet(512, 1024, 2048, 4096)]
    [int]$Resolution = 2048,
    [ValidateRange(2, 8)]
    [int]$Clusters = 4,
    [string]$ManualMask,
    [string]$Assignments,
    [ValidateSet('CPU', 'CUDA')]
    [string]$Device = 'CPU',
    [ValidateRange(0.0, 4.0)]
    [double]$NormalStrength = 1.5,
    [ValidateRange(0.0, 3.0)]
    [double]$BaseColorNormalStrength = 0.75,
    [ValidateRange(2, 64)]
    [int]$BaseColorFeatureSize = 12,
    [ValidateSet('OpenGL', 'DirectX')]
    [string]$NormalConvention = 'OpenGL',
    [ValidateRange(4, 96)]
    [int]$NormalMargin = 24,
    [switch]$AssetPortPackage,
    [ValidateSet('env', 'prop', 'wpn', 'char', 'veh', 'fx')]
    [string]$AssetPortCategory = 'env',
    [string]$AssetPortName,
    [switch]$DebugOutput,
    [switch]$SkipAO,
    [switch]$SkipPreview,
    [switch]$MapsOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$assetPath = (Resolve-Path -LiteralPath $Asset).Path

$blenderExe = $null
if ($env:BLENDER_PATH -and (Test-Path -LiteralPath $env:BLENDER_PATH)) {
    $blenderExe = $env:BLENDER_PATH
}
if (-not $blenderExe) {
    $command = Get-Command blender -ErrorAction SilentlyContinue
    if ($command) { $blenderExe = $command.Source }
}
if (-not $blenderExe) {
    $install = Get-ItemProperty `
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', `
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', `
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match '^Blender' -and $_.InstallLocation } |
        Select-Object -First 1
    if ($install) {
        $candidate = Join-Path $install.InstallLocation 'blender.exe'
        if (Test-Path -LiteralPath $candidate) { $blenderExe = $candidate }
    }
}
if (-not $blenderExe) {
    throw '找不到 Blender。请将 blender 加入 PATH，或设置 BLENDER_PATH 为 blender.exe 的完整路径。'
}

$scriptPath = Join-Path $projectRoot 'blender\pbr_resolver.py'
$arguments = @(
    '--background', '--factory-startup',
    '--python', $scriptPath,
    '--', $assetPath,
    '--resolution', $Resolution,
    '--clusters', $Clusters,
    '--device', $Device.ToLowerInvariant(),
    '--normal-strength', $NormalStrength,
    '--basecolor-normal-strength', $BaseColorNormalStrength,
    '--basecolor-feature-size', $BaseColorFeatureSize,
    '--normal-method', 'blender_bake',
    '--normal-convention', $NormalConvention.ToLowerInvariant(),
    '--normal-margin', $NormalMargin
)
if ($Output) { $arguments += @('--output', $Output) }
if ($ManualMask) { $arguments += @('--manual-mask', (Resolve-Path -LiteralPath $ManualMask).Path) }
if ($Assignments) { $arguments += @('--assignments', (Resolve-Path -LiteralPath $Assignments).Path) }
if ($DebugOutput) { $arguments += '--debug' }
if ($SkipAO) { $arguments += '--skip-ao' }
if ($SkipPreview) { $arguments += '--skip-preview' }
if ($MapsOnly) { $arguments += '--maps-only' }
if ($AssetPortPackage) {
    $arguments += @('--assetport-package', '--assetport-category', $AssetPortCategory)
    if ($AssetPortName) { $arguments += @('--assetport-name', $AssetPortName) }
}

& $blenderExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Blender 处理失败，退出码：$LASTEXITCODE"
}
