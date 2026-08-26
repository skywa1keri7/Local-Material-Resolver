param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stagingRoot = Join-Path $projectRoot 'dist\addon-staging'
$addonRoot = Join-Path $stagingRoot 'local_material_resolver'
$zipPath = Join-Path $projectRoot 'dist\LocalMaterialResolver-Blender-Addon.zip'
$projectFull = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
$stagingFull = [System.IO.Path]::GetFullPath($stagingRoot)
if (-not $stagingFull.StartsWith($projectFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝清理工作区以外的目录：$stagingFull"
}

if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $addonRoot | Out-Null

Copy-Item -LiteralPath (Join-Path $projectRoot 'blender_addon\local_material_resolver\__init__.py') -Destination $addonRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'blender\pbr_resolver.py') -Destination $addonRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'config\materials.json') -Destination $addonRoot

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $addonRoot -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "Built: $zipPath"
