import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "blender_addon"))

import bpy
import local_material_resolver

local_material_resolver.register()
assert hasattr(bpy.types.Scene, "lmr_settings")
assert hasattr(bpy.context.scene, "lmr_settings")
assert bpy.context.scene.lmr_settings.input_mode == "single"
local_material_resolver.unregister()
assert not hasattr(bpy.types.Scene, "lmr_settings")
print("Add-on registration test passed.")
