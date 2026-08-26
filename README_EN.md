# Local Material Resolver

[![Release](https://img.shields.io/github/v/release/skywa1keri7/Local-Material-Resolver?display_name=tag&sort=semver)](https://github.com/skywa1keri7/Local-Material-Resolver/releases/latest)
[![Blender](https://img.shields.io/badge/Blender-4.2%2B-F5792A?logo=blender&logoColor=white)](https://www.blender.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows&logoColor=white)](https://github.com/skywa1keri7/Local-Material-Resolver)
[![License](https://img.shields.io/github/license/skywa1keri7/Local-Material-Resolver)](LICENSE)
[![Downloads](https://img.shields.io/github/downloads/skywa1keri7/Local-Material-Resolver/total)](https://github.com/skywa1keri7/Local-Material-Resolver/releases)

**English** | [简体中文](README.md)

Local Material Resolver is a local-first Blender add-on for turning textured GLB/GLTF assets into practical PBR texture sets. It supports deterministic texture generation, batch processing, Blender/MikkTSpace normal baking, and an optional AssetPort-CN bridge for Unreal Engine 5 ingestion.

> Current version: `0.7.0`. Tested with Blender `5.2.1 LTS`; the add-on targets Blender `4.2+`. Start with a `512`-pixel test before processing production assets.

## Why This Project Exists

AI-generated and marketplace assets often arrive as a mesh, UVs, and one Base Color texture, but without a complete engine-ready material set. Preparing many such assets manually requires repetitive work: generating supporting maps, packing ORM, converting normal conventions, exporting FBX, enforcing names, and rebuilding materials in Unreal.

Local Material Resolver automates that preparation stage. It is designed as a practical asset standardization tool rather than a claim of physically exact material recovery.

## Current Features

### Blender Workflow

- Artist-friendly sidebar UI; PowerShell is not required.
- Single GLB/GLTF processing.
- Folder-based batch processing with optional recursive scanning.
- Background Blender workers keep the current scene untouched.
- Progress display, cancellation, and continue-on-error behavior.
- A new numbered output directory is created for every run.

### PBR Texture Generation

- Outputs Base Color, Ambient Occlusion, Roughness, Metallic, Detail Normal, and ORM.
- ORM layout: R=Ambient Occlusion, G=Roughness, B=Metallic.
- Uses existing material slots, a manual Material ID mask, or deterministic Base Color clustering.
- Applies configurable material priors from `config/materials.json`.
- Can generate texture-only deliverables or extended metadata and preview files.

### Detail Normal Pipeline

- Detects local dark grooves from Base Color while suppressing bright graphics and broad lighting gradients.
- Automatically reduces Base Color-derived structure when UV coverage or atlas fragmentation is unreliable.
- Combines inferred structure with object-space procedural microdetail.
- Uses Blender normal baking and MikkTSpace tangent-space output instead of directly exporting grayscale derivatives.
- Supports OpenGL/Blender and DirectX/Unreal normal conventions.
- Adds configurable UV padding and flat boundary protection.

### AssetPort-CN / Unreal Engine 5 Bridge

- Exports a triangulated static-mesh FBX with tangents.
- Generates AssetPort-compatible Base Colour, Normal, and ORM filenames.
- Always writes the bridge normal as DirectX, without changing the normal convention of the regular output.
- Supports Environment, Props, Weapons, Characters, Vehicles, and Effects routing categories.
- Collects all generated UE files into one import directory during batch processing.
- Sanitizes ambiguous asset names so AssetPort does not mistake the last underscore-delimited token for a material slot.

## Pipeline

```text
Textured GLB / GLTF
        ↓
Local Material Resolver (Blender)
        ↓
BaseColor + AO + Roughness + Metallic + Normal + ORM
        ↓
AssetPort Import Package (optional)
        ↓
AssetPort-CN → Unreal Engine 5
```

The tool does not require a local VLM, a cloud model, or an external API. Image processing, mesh inspection, texture baking, and FBX export run locally through Blender.

## Installation

Download the latest package from [GitHub Releases](https://github.com/skywa1keri7/Local-Material-Resolver/releases/latest), or use the repository copy at [`dist/LocalMaterialResolver-Blender-Addon.zip`](dist/LocalMaterialResolver-Blender-Addon.zip).

1. Open Blender.
2. Go to `Edit > Preferences > Add-ons`.
3. Open the menu in the upper-right corner and choose `Install from Disk`.
4. Select the ZIP file without extracting it.
5. Enable `Local Material Resolver`.
6. Return to the 3D Viewport and press `N`.
7. Open the `PBR Resolver` tab.

## Single-Asset Processing

1. Select `Single Asset` at the top of the panel.
2. Choose a textured GLB/GLTF with a mesh and UV0.
3. Use `512` for the first test.
4. Click `Generate PBR`.

The default texture-only output is written beside the source asset:

```text
<AssetName>_PBR_Maps/
```

Existing results are preserved. Later runs create `_001`, `_002`, and subsequent numbered directories.

## Batch Processing

1. Select `Batch Folder`.
2. Choose a folder containing GLB/GLTF files.
3. Optionally enable recursive subfolder scanning.
4. Choose an output root, or leave it empty to use the input folder.
5. Click `Batch Generate PBR`.

Each batch receives an independent root:

```text
PBR_Batch/
├─ AssetA_PBR_Maps/
├─ AssetB_PBR_Maps/
└─ AssetC_PBR_Maps/
```

Later runs create `PBR_Batch_001`, `PBR_Batch_002`, and so on. One failed asset does not prevent the remaining queue from running.

## AssetPort-CN Bridge

Enable `Generate AssetPort / UE5 Import Package` and select a UE category. The bridge generates:

```text
AssetPort_Import/
├─ SM_env_RoofPanel.fbx
├─ T_env_RoofPanel_D.png
├─ T_env_RoofPanel_N.png
└─ T_env_RoofPanel_ORM.png
```

Bridge conventions:

- `_D`: Base Colour, sRGB.
- `_N`: DirectX tangent-space Normal.
- `_ORM`: R=AO, G=Roughness, B=Metallic.
- FBX: triangulated mesh with UVs and tangents.

For single assets, `AssetPort_Import` is stored inside the asset result. For batches, all bridge files are collected under `PBR_Batch/AssetPort_Import/`. Select that directory in AssetPort-CN to continue with UE import, texture configuration, material creation, and mesh assignment.

## Output Files

Texture-only mode produces:

```text
basecolor.png
ao.png
roughness.png
metallic.png
detail_normal.png
ORM.png
```

When texture-only mode is disabled, the tool can additionally produce:

- `material_id.png`
- `material_assignments.json`
- `asset_info.json`
- `material.json`
- `timings.json`
- Debug masks
- `preview.png`
- `material_preview.blend`

## Material Region Sources

Material regions are selected in this order:

1. A manual grayscale Material ID mask.
2. Existing GLB material slots.
3. Deterministic Base Color clustering.

Extended output includes `material_assignments.json`, which can be edited and supplied on a later run. Available material presets are defined in [`config/materials.json`](config/materials.json). Unknown regions fall back to `generic_dielectric`.

## Important Interpretation Notes

Base Color alone cannot uniquely determine physical surface properties. A dark feature may be a groove, dirt, baked lighting, or printed graphics. Roughness, Metallic, and inferred structure are therefore configurable priors, not measured ground truth.

Tangent-space normal maps may show different purple/blue directions between UV islands. That color change is not automatically a rendering seam; evaluate continuity under lighting on the model.

## Command-Line Usage

```powershell
.\run.ps1 -Asset .\asset.glb -Resolution 1024 -MapsOnly
```

AssetPort package:

```powershell
.\run.ps1 `
  -Asset .\asset.glb `
  -Resolution 2048 `
  -MapsOnly `
  -AssetPortPackage `
  -AssetPortCategory env `
  -AssetPortName RoofPanel
```

Common parameters:

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

## Validation

Run the end-to-end smoke test:

```powershell
.\scripts\smoke_test.ps1
```

The repository includes coverage for:

- Base Color groove extraction.
- Blender add-on registration.
- Single and batch background jobs.
- FBX re-import and UV preservation.
- OpenGL-to-DirectX normal conversion.
- AssetPort-compatible naming and batch collision handling.
- Texture dimensions, ranges, packed channels, and metadata.

## Current Limitations

- The current workflow primarily targets static GLB/GLTF meshes.
- Every mesh requires a valid UV0, preferably inside 0–1 and without overlap.
- Multiple independent Base Color textures, UDIM, skinning, and overlapping UVs are not fully supported.
- Roughness and Metallic are physically reasonable heuristics, not recovered measurements.
- Dirt and local shadows may still be mistaken for grooves.
- The AssetPort bridge currently exports static FBX only; skeletal meshes, animation, and LOD export are not connected.
- FBX material slot names are preserved, but independent generated texture sets per slot are not yet supported.
- Production UE projects should still review scale, normals, collision, Lightmap UVs, and final materials.

## Roadmap

- [ ] End-to-end validation across multiple UE5 and AssetPort-CN versions.
- [ ] Asset-level presets for metal, plastic, stone, wood, and fabric.
- [ ] Automated UV overlap, normal, and missing-texture quality checks.
- [ ] Independent texture sets for multiple material slots.
- [ ] Alpha, Masked, and Translucent workflows.
- [ ] LOD and collision export.
- [ ] Optional AssetPort manifest support.
- [ ] Expanded bilingual UI and documentation.

## License

Local Material Resolver is released under the [MIT License](LICENSE).
