import math
import os
import sys

import bpy


def args_after_separator():
    if "--" not in sys.argv:
        raise SystemExit("Expected output GLB path after --")
    return sys.argv[sys.argv.index("--") + 1 :]


output_path = os.path.abspath(args_after_separator()[0])
os.makedirs(os.path.dirname(output_path), exist_ok=True)

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

# A bevelled cube with a known non-overlapping UV atlas and one BaseColor image.
bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 0.0))
obj = bpy.context.active_object
obj.name = "SyntheticPaintedBlock"
obj.scale = (1.4, 0.45, 1.0)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

bevel = obj.modifiers.new("Small bevel", "BEVEL")
bevel.width = 0.08
bevel.segments = 3
bpy.context.view_layer.objects.active = obj
bpy.ops.object.modifier_apply(modifier=bevel.name)

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.04)
bpy.ops.object.mode_set(mode="OBJECT")

size = 512
image = bpy.data.images.new("Synthetic_BaseColor", width=size, height=size, alpha=True)
pixels = [0.0] * (size * size * 4)
for y in range(size):
    for x in range(size):
        i = (y * size + x) * 4
        checker = ((x // 64) + (y // 64)) % 2
        stripe = 0.10 if (x // 16) % 2 == 0 else 0.0
        pixels[i + 0] = 0.24 + stripe + checker * 0.05
        pixels[i + 1] = 0.055 + checker * 0.018
        pixels[i + 2] = 0.025
        pixels[i + 3] = 1.0
image.pixels.foreach_set(pixels)
image.filepath_raw = os.path.join(os.path.dirname(output_path), "synthetic_basecolor.png")
image.file_format = "PNG"
image.save()

material = bpy.data.materials.new("Painted_Metal_Red")
material.use_nodes = True
nodes = material.node_tree.nodes
links = material.node_tree.links
principled = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
tex = nodes.new("ShaderNodeTexImage")
tex.image = image
links.new(tex.outputs["Color"], principled.inputs["Base Color"])
principled.inputs["Roughness"].default_value = 0.4
obj.data.materials.append(material)

bpy.ops.wm.save_as_mainfile(filepath=os.path.splitext(output_path)[0] + ".blend")
bpy.ops.export_scene.gltf(
    filepath=output_path,
    export_format="GLB",
    use_selection=False,
    export_apply=True,
)
print(f"Created synthetic test asset: {output_path}")

