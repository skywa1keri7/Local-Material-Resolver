[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$blenderExe = 'E:\SteamLibrary\steamapps\common\Blender\blender.exe'
if (-not (Test-Path -LiteralPath $blenderExe)) {
    if ($env:BLENDER_PATH -and (Test-Path -LiteralPath $env:BLENDER_PATH)) {
        $blenderExe = $env:BLENDER_PATH
    } else {
        throw '找不到测试所需的 Blender。请设置 BLENDER_PATH。'
    }
}

$fixtureDir = Join-Path $projectRoot 'test-assets'
$assetPath = Join-Path $fixtureDir 'synthetic_asset.glb'
New-Item -ItemType Directory -Force -Path $fixtureDir | Out-Null

& $blenderExe --background --factory-startup `
    --python (Join-Path $projectRoot 'tests\create_test_asset.py') -- `
    $assetPath
if ($LASTEXITCODE -ne 0) { throw '创建测试资产失败。' }

& (Join-Path $projectRoot 'run.ps1') `
    -Asset $assetPath `
    -Output (Join-Path $projectRoot 'test-output\synthetic_asset') `
    -Resolution 512 `
    -Clusters 3 `
    -DebugOutput

$required = @(
    'basecolor.png', 'ao.png', 'roughness.png', 'metallic.png',
    'detail_normal.png', 'ORM.png', 'material_id.png',
    'asset_info.json', 'material.json', 'timings.json',
    'material_preview.blend', 'preview.png'
)
foreach ($name in $required) {
    $path = Join-Path $projectRoot "test-output\synthetic_asset\$name"
    if (-not (Test-Path -LiteralPath $path)) { throw "缺少输出：$name" }
}

& $blenderExe --background --factory-startup `
    --python (Join-Path $projectRoot 'tests\validate_outputs.py') -- `
    (Join-Path $projectRoot 'test-output\synthetic_asset')
if ($LASTEXITCODE -ne 0) { throw '输出数值验证失败。' }

Write-Host 'Smoke test passed.' -ForegroundColor Green
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'test-output\synthetic_asset') |
    Select-Object Name, Length
