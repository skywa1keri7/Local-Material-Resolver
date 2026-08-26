import sys
import shutil
from pathlib import Path

import bpy
import numpy as np

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "dist" / "addon-staging"))

import local_material_resolver as addon

addon.register()
settings = bpy.context.scene.lmr_settings
settings.asset_path = str(root / "test-assets" / "synthetic_asset.glb")
output_root = root / "test-output" / "addon_job_unique_root"
settings.output_dir = str(output_root)
settings.resolution = "512"
settings.clusters = 3
settings.skip_ao = True
settings.skip_preview = True
settings.maps_only = True
settings.auto_open = False
settings.assetport_package = True
settings.assetport_category = "env"
settings.assetport_name = "BridgeCube"

result = bpy.ops.lmr.process()
assert result == {"FINISHED"}, result
assert addon._job is not None
code = addon._job["process"].wait(timeout=60)
addon._poll_job()
assert code == 0, code
output_dir = Path(settings.last_output)
assert output_dir.parent == output_root.resolve(), output_dir
expected = {
    "basecolor.png",
    "ao.png",
    "roughness.png",
    "metallic.png",
    "detail_normal.png",
    "ORM.png",
}
assert {path.name for path in output_dir.iterdir() if path.is_file()} == expected
package_dir = output_dir / "AssetPort_Import"
package_expected = {
    "SM_env_BridgeCube.fbx",
    "T_env_BridgeCube_D.png",
    "T_env_BridgeCube_N.png",
    "T_env_BridgeCube_ORM.png",
}
assert {path.name for path in package_dir.iterdir()} == package_expected
assert all(path.stat().st_size > 0 for path in package_dir.iterdir())

source_image = bpy.data.images.load(str(output_dir / "detail_normal.png"), check_existing=False)
ue_image = bpy.data.images.load(str(package_dir / "T_env_BridgeCube_N.png"), check_existing=False)
source_image.colorspace_settings.name = "Non-Color"
ue_image.colorspace_settings.name = "Non-Color"
source_pixels = np.empty(len(source_image.pixels), dtype=np.float32)
ue_pixels = np.empty(len(ue_image.pixels), dtype=np.float32)
source_image.pixels.foreach_get(source_pixels)
ue_image.pixels.foreach_get(ue_pixels)
source_pixels = source_pixels.reshape((-1, 4))
ue_pixels = ue_pixels.reshape((-1, 4))
assert np.max(np.abs(source_pixels[:, 0] - ue_pixels[:, 0])) < 0.01
assert np.max(np.abs(source_pixels[:, 2] - ue_pixels[:, 2])) < 0.01
assert np.max(np.abs((1.0 - source_pixels[:, 1]) - ue_pixels[:, 1])) < 0.01

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.fbx(filepath=str(package_dir / "SM_env_BridgeCube.fbx"))
imported_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
assert imported_meshes
assert all(obj.data.uv_layers.active is not None for obj in imported_meshes)
assert "完成" in settings.status, settings.status

settings.input_mode = "batch"
batch_input = root / "test-output" / "addon_batch_input"
batch_input.mkdir(parents=True, exist_ok=True)
shutil.copyfile(root / "test-assets" / "synthetic_asset.glb", batch_input / "asset_a.glb")
shutil.copyfile(root / "test-assets" / "synthetic_asset.glb", batch_input / "asset_b.glb")
settings.batch_input_dir = str(batch_input)
settings.batch_recursive = False
result = bpy.ops.lmr.process()
assert result == {"FINISHED"}, result
assert addon._job is not None
while addon._job is not None:
    code = addon._job["process"].wait(timeout=60)
    addon._poll_job()
    assert code == 0, code

batch_root = Path(settings.last_output)
assert batch_root.parent == output_root.resolve(), batch_root
asset_outputs = [
    path for path in batch_root.iterdir()
    if path.is_dir() and path.name != "AssetPort_Import"
]
assert len(asset_outputs) == 2, asset_outputs
for asset_output in asset_outputs:
    assert {path.name for path in asset_output.iterdir()} == expected
batch_package = batch_root / "AssetPort_Import"
assert {path.name for path in batch_package.iterdir()} == {
    "SM_env_AssetA.fbx",
    "T_env_AssetA_D.png",
    "T_env_AssetA_N.png",
    "T_env_AssetA_ORM.png",
    "SM_env_AssetB.fbx",
    "T_env_AssetB_D.png",
    "T_env_AssetB_N.png",
    "T_env_AssetB_ORM.png",
}
assert "批量完成：成功 2，失败 0" == settings.status, settings.status
addon.unregister()
print("Add-on single and batch background job tests passed.")
