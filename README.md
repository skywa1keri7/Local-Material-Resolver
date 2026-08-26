# Local Material Resolver

Local Material Resolver 是一个面向 Blender 与 Unreal Engine 5 的本地优先 PBR 资产处理工具。它可以从已有 GLB/GLTF、UV 和 BaseColor 中生成基础 PBR 贴图，并通过 AssetPort-CN 桥接包将处理结果送入 UE5。

Local Material Resolver is a local-first Blender tool for rebuilding practical PBR texture sets from textured GLB/GLTF assets, with an optional AssetPort-CN bridge for Unreal Engine 5 ingestion.

> 当前版本：`0.7.0`。已在 Blender `5.2.1 LTS` 测试，插件最低目标版本为 Blender `4.2`。建议先使用 `512` 分辨率在测试资产上确认效果。

## 当前功能

- Blender 侧边栏图形界面，不要求美术使用 PowerShell。
- 单个 GLB/GLTF 处理与文件夹批量处理。
- 批量任务支持递归扫描、进度提示、停止任务和失败后继续。
- 每次运行创建新的编号目录，不覆盖既有结果。
- 生成 BaseColor、AO、Roughness、Metallic、Detail Normal 和 ORM。
- ORM 通道约定为 R=AO、G=Roughness、B=Metallic。
- 从 BaseColor 局部暗线提取候选凹槽，同时过滤亮色图案和大范围明暗渐变。
- 根据 UV 覆盖率和碎片化程度自动降低不可靠结构的影响。
- 使用 Blender 与 MikkTSpace 烘焙切线空间法线，而不是直接输出灰度图梯度。
- 支持 OpenGL / Blender 与 DirectX / Unreal 法线格式。
- 支持只输出贴图，以及生成 Blend 与工作室预览。
- 支持 AssetPort-CN / UE5 松耦合桥接。

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

本工具不依赖本地 VLM、云端模型或外部 API。所有图像处理、几何分析和烘焙都在本地 Blender 中完成。

## 安装

安装包：[`dist/LocalMaterialResolver-Blender-Addon.zip`](dist/LocalMaterialResolver-Blender-Addon.zip)

1. 打开 Blender。
2. 进入 `Edit > Preferences > Add-ons`。
3. 点击右上角菜单，选择 `Install from Disk`。
4. 选择完整 ZIP 文件，不要解压。
5. 启用 `Local Material Resolver`。
6. 回到 3D 视图，按 `N` 打开右侧栏。
7. 选择 `PBR Resolver` 标签页。

插件会启动独立的后台 Blender 进程，不会清空或修改美术当前打开的场景。

## 单个资产处理

1. 在面板顶部选择“单个资产”。
2. 选择带 Mesh、UV 和 BaseColor 的 GLB/GLTF。
3. 第一次建议选择 `512`。
4. 点击“生成 PBR”。

默认启用“仅输出贴图”，结果写入资产旁边的：

```text
<AssetName>_PBR_Maps/
```

再次处理会依次创建：

```text
<AssetName>_PBR_Maps_001/
<AssetName>_PBR_Maps_002/
```

## 批量处理

1. 在面板顶部选择“批量文件夹”。
2. 选择包含 GLB/GLTF 的输入文件夹。
3. 根据需要启用“包含子文件夹”。
4. 可指定输出根目录，留空则写入输入文件夹。
5. 点击“批量生成 PBR”。

每次批处理会创建独立总目录：

```text
PBR_Batch/
├─ AssetA_PBR_Maps/
├─ AssetB_PBR_Maps/
└─ AssetC_PBR_Maps/
```

再次运行会创建 `PBR_Batch_001`、`PBR_Batch_002` 等目录。单个资产失败不会阻止后续任务继续处理。

## AssetPort-CN / UE5 桥接

启用“生成 AssetPort / UE5 导入包”后，工具会额外生成 AssetPort-CN 可直接扫描的目录：

```text
AssetPort_Import/
├─ SM_env_RoofPanel.fbx
├─ T_env_RoofPanel_D.png
├─ T_env_RoofPanel_N.png
└─ T_env_RoofPanel_ORM.png
```

桥接规则：

- FBX 固定三角化并导出切线。
- `_D` 为 Base Colour。
- `_N` 始终转换为 UE5 所需的 DirectX 法线。
- `_ORM` 为 R=AO、G=Roughness、B=Metallic。
- 支持 `Environment`、`Props`、`Weapons`、`Characters`、`Vehicles` 和 `Effects` 分类。
- 批量模式会将所有 UE 文件集中到本次 `PBR_Batch/AssetPort_Import/`。
- 为避免 AssetPort 将最后一个下划线段识别为材质槽，桥接名称会自动移除分隔歧义，例如 `roof_panel` 输出为 `RoofPanel`。

在 AssetPort-CN 中选择生成的 `AssetPort_Import` 文件夹即可执行后续 UE5 导入、纹理设置、材质创建与网格绑定。

## 输出贴图

启用“仅输出贴图”时，每个普通结果目录包含：

```text
basecolor.png
ao.png
roughness.png
metallic.png
detail_normal.png
ORM.png
```

关闭“仅输出贴图”后，还会生成 Material ID、材质分配 JSON、资产分析、耗时数据、调试图、工作室预览和预览 Blend 文件。

## 法线与颜色结构

“颜色缝隙强度”根据 BaseColor 中的局部暗线生成候选凹槽。算法只接受局部暗结构，并抑制亮色图案、宽范围光照变化和 UV 边界影响。

颜色不能唯一决定真实几何。暗色可能来自缝隙、污迹、阴影或印刷图案，因此该结果属于可调节的材质先验，而不是真实表面反演。将“颜色缝隙强度”设置为 `0` 可以完全关闭此功能。

法线贴图在不同 UV 岛内出现不同紫蓝色方向属于正常的切线空间编码现象，应以模型渲染时的光照连续性判断是否存在接缝。

## 材质区域

区域来源按以下优先级选择：

1. 手工灰度 Material ID Mask。
2. GLB 原有 Material Slot。
3. BaseColor 确定性颜色聚类。

非“仅贴图”模式会输出 `material_assignments.json`。可以修改其中的材质类型，再作为高级设置输入重新生成。

可用材质类型定义在 [`config/materials.json`](config/materials.json)。程序不会仅凭颜色可靠判断金属、木材或塑料；无法判断时使用 `generic_dielectric`。

## 开发与批处理命令

PowerShell：

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

不指定 `-Output` 时会在资产旁创建新的编号目录。

## 常用参数

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

创建合成测试资产并运行完整流程：

```powershell
.\scripts\smoke_test.ps1
```

项目同时包含：

- BaseColor 暗缝提取测试。
- Blender 插件注册测试。
- 单个与批量后台任务测试。
- FBX 回读与 UV 验证。
- OpenGL 到 DirectX 法线转换验证。
- AssetPort 兼容命名与批量冲突测试。

## 当前限制

- 当前输入主要面向静态 GLB/GLTF Mesh。
- 每个 Mesh 需要有效 UV0，且应尽量位于 0–1、避免重叠。
- 多张独立 BaseColor、UDIM、蒙皮和重叠 UV 暂不保证正确结果。
- Roughness 与 Metallic 是物理合理的启发式先验，不是真实测量值。
- BaseColor 中的污迹或局部阴影仍可能被误认为凹槽。
- AssetPort 桥接当前导出静态 FBX；骨骼、动画和 LOD 尚未接入。
- 多材质槽名称会保留在 FBX 中，但尚不能自动为每个槽生成独立纹理集。
- 实际 UE5 项目仍应检查法线、比例、碰撞、Lightmap UV 和材质结果。

## 开发计划

- [ ] UE5 / AssetPort-CN 多版本端到端验证。
- [ ] 金属、塑料、石材、木材和布料资产级预设。
- [ ] UV 重叠、法线异常和贴图缺失自动质检。
- [ ] 多材质槽独立纹理集。
- [ ] Alpha、Masked 与 Translucent 工作流。
- [ ] LOD 与碰撞导出。
- [ ] 可选 AssetPort Manifest，减少对文件名推断的依赖。
- [ ] 更完整的中英文界面与文档。

