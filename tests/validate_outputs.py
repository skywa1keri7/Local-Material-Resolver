import json
import os
import sys

import bpy
import numpy as np


def args_after_separator():
    if "--" not in sys.argv:
        raise SystemExit("Expected output directory after --")
    return sys.argv[sys.argv.index("--") + 1 :]


output_dir = os.path.abspath(args_after_separator()[0])
resolution = 512


def load(name):
    image = bpy.data.images.load(os.path.join(output_dir, name), check_existing=False)
    try:
        image.colorspace_settings.name = "Non-Color"
    except Exception:
        image.colorspace_settings.name = "Raw"
    width, height = map(int, image.size)
    assert (width, height) == (resolution, resolution), (name, width, height)
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape((height, width, 4))


ao = load("ao.png")
roughness = load("roughness.png")
metallic = load("metallic.png")
normal = load("detail_normal.png")
orm = load("ORM.png")
material_id = load("material_id.png")

for name, array in (
    ("ao", ao),
    ("roughness", roughness),
    ("metallic", metallic),
    ("normal", normal),
    ("orm", orm),
):
    assert np.all(np.isfinite(array)), f"{name} contains non-finite values"
    assert float(np.min(array)) >= 0.0 and float(np.max(array)) <= 1.0, name

tolerance = 2.5 / 255.0
assert np.max(np.abs(orm[..., 0] - ao[..., 0])) <= tolerance, "ORM.R != AO"
assert np.max(np.abs(orm[..., 1] - roughness[..., 0])) <= tolerance, "ORM.G != Roughness"
assert np.max(np.abs(orm[..., 2] - metallic[..., 0])) <= tolerance, "ORM.B != Metallic"
assert float(np.min(normal[..., 2])) > 0.5, "Normal Z must face outward"

ids = np.rint(material_id[..., 0] * 255.0).astype(np.uint8)
present = sorted(int(value) for value in np.unique(ids) if value != 255)
assert present == [0, 1, 2], present
covered = ids != 255
normal_xy_deviation = np.sqrt(
    (normal[..., 0] - 0.5) ** 2 + (normal[..., 1] - 0.5) ** 2
)
assert float(np.percentile(normal_xy_deviation[covered], 75)) > 2.0 / 255.0, "Detail normal is visually flat"

with open(os.path.join(output_dir, "material.json"), "r", encoding="utf-8") as handle:
    metadata = json.load(handle)
assert metadata["resolution"] == resolution
assert metadata["region_source"] == "color_clusters"
assert len(metadata["materials"]) == 3

print(
    json.dumps(
        {
            "validated": True,
            "resolution": resolution,
            "ids": present,
            "roughness_range": [float(np.min(roughness[..., 0])), float(np.max(roughness[..., 0]))],
            "metallic_range": [float(np.min(metallic[..., 0])), float(np.max(metallic[..., 0]))],
            "ao_range": [float(np.min(ao[..., 0])), float(np.max(ao[..., 0]))],
        },
        indent=2,
    )
)
