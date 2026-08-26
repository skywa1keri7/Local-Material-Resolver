# Local Material Resolver

[![Release](https://img.shields.io/github/v/release/skywa1keri7/Local-Material-Resolver?display_name=tag&sort=semver)](https://github.com/skywa1keri7/Local-Material-Resolver/releases/latest)
[![Blender](https://img.shields.io/badge/Blender-4.2%2B-F5792A?logo=blender&logoColor=white)](https://www.blender.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows&logoColor=white)](https://github.com/skywa1keri7/Local-Material-Resolver)
[![License](https://img.shields.io/github/license/skywa1keri7/Local-Material-Resolver)](LICENSE)

**简体中文** | [English](README_EN.md)

Local Material Resolver 是一个面向 Blender 与 Unreal Engine 5 的本地优先 PBR 资产处理工具。它可以从已有 GLB/GLTF、UV 和 BaseColor 中生成实用的 PBR 贴图，并通过 AssetPort-CN 桥接包将处理结果送入 UE5。

> 当前版本：`0.7.0`。已在 Blender `5.2.1 LTS` 测试，插件最低目标版本为 Blender `4.2`。建议先使用 `512` 分辨率在测试资产上确认效果。

## 项目定位

AI 生成资产和素材库模型经常只有 Mesh、UV 与一张 BaseColor，缺少完整的引擎材质。逐个补贴图、打包 ORM、转换法线格式、导出 FBX、统一命名并在 UE 中重建材质，会产生大量重复劳动。

Local Material Resolver 自动完成这段“资产标准化”流程。它的目标是批量生成可用、统一、可交付的 PBR 材质先验，并衔接 UE5 资产入库；它并不声称能够从单张颜色图精确恢复真实物理材质。

## 当前功能

### Blender 工作流

- Blender 侧边栏图形界面，美术不需要使用 PowerShell。
- 支持单个 GLB/GLTF 处理。
- 支持文件夹批量处理与递归扫描。
- 后台 Blender 独立运行，不清空或修改当前打开的场景。
- 支持任务进度、停止处理和失败后继续。
- 每次运行创建新的编号目录，不覆盖既有结果。

### PBR 贴图生成

- 生成 BaseColor、Ambient Occlusion、Roughness、Metallic、Detail Normal 和 ORM。
- ORM 通道固定为 R=AO、G=Roughness、B=Metallic。
- 支持原有 Material Slot、手工 Material ID Mask 和确定性颜色聚类。
- 使用 `config/materials.json` 中的材质先验生成粗糙度、金属度和细节强度。
- 支持只输出交付贴图，或生成包含分析、调试与预览的扩展结果。

### Detail Normal 管线

- 从 BaseColor 中检测局部暗色凹槽候选。
- 抑制亮色图案、宽范围明暗变化和 atlas padding 干扰。
- 根据 UV 覆盖效率与碎片化程度自动降低不可靠结构的强度。
- 将 BaseColor 结构与物体空间程序化微表面组合为高度来源。
- 使用 Blender 与 MikkTSpace 烘焙切线空间法线，而不是直接输出灰度图导数。
- 支持 OpenGL / Blender 与 DirectX / Unreal 法线格式。
- 支持可调 UV Padding 和边界平坦保护。

### 批量处理

- 按稳定顺序逐个处理资产，避免同时启动大量 Blender 进程争抢资源。
- 显示当前序号、文件名、单资产耗时和最终成功/失败数量。
- 单个资产失败后记录日志，并继续处理后续资产。
- 每次批量任务创建独立的 `PBR_Batch` 编号目录。

### AssetPort-CN / UE5 桥接

- 自动导出三角化、带 UV 与切线的静态 FBX。
- 自动输出 AssetPort 可识别的 Base Colour、Normal 与 ORM 命名。
- UE 桥接法线始终转换为 DirectX，不改变普通 PBR 输出的法线约定。
- 支持 Environment、Props、Weapons、Characters、Vehicles 和 Effects 分类。
- 批量模式将所有 UE 文件集中到一个 `AssetPort_Import` 目录。
- 自动清理有歧义的资产名，避免 AssetPort 把最后一个下划线段误认为材质槽。

## 工作流

```text
Textured GLB / GLTF
        ↓
Local Material Resolver（Blender）
        ↓
BaseColor + AO + Roughness + Metallic + Normal + ORM
        ↓
AssetPort Import Package（可选）
        ↓
AssetPort-CN → Unreal Engine 5
```

本工具不依赖本地 VLM、云端模型或外部 API。图像处理、模型检查、贴图烘焙和 FBX 导出都通过本地 Blender 完成。

## 安装

从 [GitHub Releases](https://github.com/skywa1keri7/Local-Material-Resolver/releases/latest) 下载最新版，或使用仓库内的 [`dist/LocalMaterialResolver-Blender-Addon.zip`](dist/LocalMaterialResolver-Blender-Addon.zip)。

1. 打开 Blender。
2. 进入 `Edit > Preferences > Add-ons`。
3. 点击右上角菜单，选择 `Install from Disk`。
4. 选择完整 ZIP 文件，不要解压。
5. 启用 `Local Material Resolver`。
6. 回到 3D 视图，按 `N` 打开右侧栏。
7. 选择 `PBR Resolver` 标签页。

## 单个资产处理

1. 在面板顶部选择“单个资产”。
2. 选择带 Mesh、UV0 和 BaseColor 的 GLB/GLTF。
3. 第一次建议选择 `512`。
4. 点击“生成 PBR”。

默认结果写入资产旁边：

```text
<AssetName>_PBR_Maps/
```

已有结果不会被覆盖，后续运行依次创建 `_001`、`_002` 等编号目录。

## 批量处理

1. 在面板顶部选择“批量文件夹”。
2. 选择包含 GLB/GLTF 的输入文件夹。
3. 根据需要启用“包含子文件夹”。
4. 可指定输出根目录，留空则使用输入文件夹。
5. 点击“批量生成 PBR”。

输出结构：

```text
PBR_Batch/
├─ AssetA_PBR_Maps/
├─ AssetB_PBR_Maps/
└─ AssetC_PBR_Maps/
```

后续运行会创建 `PBR_Batch_001`、`PBR_Batch_002` 等目录。

## AssetPort-CN / UE5 桥接

启用“生成 AssetPort / UE5 导入包”并选择 UE 分类后，会额外生成：

```text
AssetPort_Import/
├─ SM_env_RoofPanel.fbx
├─ T_env_RoofPanel_D.png
├─ T_env_RoofPanel_N.png
└─ T_env_RoofPanel_ORM.png
```

桥接约定：

- `_D`：Base Colour，sRGB。
- `_N`：DirectX 切线空间 Normal。
- `_ORM`：R=AO、G=Roughness、B=Metallic。
- FBX：静态 Mesh、固定三角化、保留 UV 与切线。

单个模式下，`AssetPort_Import` 位于资产结果目录内；批量模式下，所有桥接文件集中到 `PBR_Batch/AssetPort_Import/`。在 AssetPort-CN 中选择该目录，即可继续完成 UE 导入、纹理设置、材质创建和网格绑定。

## 输出文件

“仅输出贴图”模式生成：

```text
basecolor.png
ao.png
roughness.png
metallic.png
detail_normal.png
ORM.png
```

关闭“仅输出贴图”后，还可以生成：

- `material_id.png`
- `material_assignments.json`
- `asset_info.json`
- `material.json`
- `timings.json`
- 调试 Mask
- `preview.png`
- `material_preview.blend`

## 材质区域来源

区域按以下优先级选择：

1. 手工灰度 Material ID Mask。
2. GLB 已有 Material Slot。
3. BaseColor 确定性颜色聚类。

扩展输出会生成 `material_assignments.json`，可以修改后在下一次运行时重新载入。可用材质类型定义在 [`config/materials.json`](config/materials.json)，未知区域使用 `generic_dielectric`。

## 参数说明

- “细节法线强度”：控制程序化微表面的整体强度。
- “颜色缝隙强度”：控制从 BaseColor 暗线推导的候选凹槽；设为 `0` 可关闭。
- “暗缝宽度”：目标暗线尺度，以 2048 贴图像素为基准。
- “法线格式”：普通输出使用 OpenGL 或 DirectX；AssetPort 包固定使用 DirectX。
- “法线 Padding”：控制 UV 岛向外扩张的烘焙边距。
- “颜色区域数”：没有 Material Slot 或手工 Mask 时的聚类区域数量。
- “AO 设备”：选择 CPU 或 NVIDIA CUDA。

## 结果应该如何理解

BaseColor 不能唯一决定真实物理属性。暗色可能来自缝隙、污迹、阴影或印刷图案，因此 Roughness、Metallic 和颜色结构都属于可调整的材质先验，而不是真实测量值。

切线空间法线图在不同 UV 岛中可能显示不同的紫蓝色方向。这种颜色变化不一定是渲染接缝，应在模型上通过光照连续性判断。

## 命令行使用

```powershell
.\run.ps1 -Asset .\asset.glb -Resolution 1024 -MapsOnly
```

生成 AssetPort 桥接包：

```powershell
.\run.ps1 `
  -Asset .\asset.glb `
  -Resolution 2048 `
  -MapsOnly `
  -AssetPortPackage `
  -AssetPortCategory env `
  -AssetPortName RoofPanel
```

常用参数：

```text
-Resolution 512|1024|2048|4096
-Clusters 2..8
-ManualMask path.png
-Assignments path.json
-Output path
-Device CPU|CUDA
-NormalStrength 0..4
-BaseColorNormalStrength 0..3
-BaseColorFeatureSize 2..64
-NormalConvention OpenGL|DirectX
-NormalMargin 4..96
-MapsOnly
-DebugOutput
-SkipAO
-SkipPreview
-AssetPortPackage
-AssetPortCategory env|prop|wpn|char|veh|fx
-AssetPortName AssetName
```

## 验证

运行完整 Smoke Test：

```powershell
.\scripts\smoke_test.ps1
```

项目测试覆盖：

- BaseColor 暗缝提取。
- Blender 插件注册。
- 单个与批量后台任务。
- FBX 回读与 UV 保留。
- OpenGL 到 DirectX 法线转换。
- AssetPort 兼容命名与批量冲突处理。
- 贴图尺寸、数值范围、通道打包与元数据。

## 当前限制

- 当前流程主要面向静态 GLB/GLTF Mesh。
- 每个 Mesh 需要有效 UV0，并应尽量位于 0–1、避免重叠。
- 多张独立 BaseColor、UDIM、蒙皮和重叠 UV 暂未完整支持。
- Roughness 与 Metallic 是物理合理的启发式先验，不是真实测量结果。
- 污迹和局部阴影仍可能被误认为凹槽。
- AssetPort 桥接目前只导出静态 FBX，尚未接入骨骼、动画和 LOD。
- FBX 会保留材质槽名称，但尚不能为每个槽自动生成独立贴图集。
- 正式 UE 项目仍应检查比例、法线、碰撞、Lightmap UV 和最终材质。

## 开发计划

- [ ] UE5 / AssetPort-CN 多版本端到端验证。
- [ ] 金属、塑料、石材、木材和布料资产级预设。
- [ ] UV 重叠、法线异常和贴图缺失自动质检。
- [ ] 多材质槽独立纹理集。
- [ ] Alpha、Masked 与 Translucent 工作流。
- [ ] LOD 与碰撞导出。
- [ ] 可选 AssetPort Manifest。
- [ ] 更完整的双语界面与文档。

## 开源许可

Local Material Resolver 使用 [MIT License](LICENSE) 开源。
