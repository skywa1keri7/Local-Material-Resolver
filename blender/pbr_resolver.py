"""Blender-headless deterministic PBR resolver.

Run with:
    blender --background --factory-startup --python pbr_resolver.py -- asset.glb

The implementation deliberately avoids external Python packages. NumPy ships with
Blender and is used for image processing; bpy handles import, baking and preview.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

import bpy
import numpy as np


UNKNOWN_ID = 255
SCRIPT_ROOT = Path(__file__).resolve().parent.parent


class PipelineError(RuntimeError):
    pass


class StageTimer:
    def __init__(self):
        self.started = time.perf_counter()
        self.last = self.started
        self.values: dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.values[name] = round(now - self.last, 3)
        self.last = now
        print(f"[LMR] {name:<22} {self.values[name]:>8.3f} s")

    def finish(self) -> None:
        self.values["total"] = round(time.perf_counter() - self.started, 3)
        print(f"[LMR] {'total':<22} {self.values['total']:>8.3f} s")


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Generate deterministic PBR maps from a GLB")
    parser.add_argument("asset")
    parser.add_argument("--output")
    parser.add_argument("--resolution", type=int, choices=(512, 1024, 2048, 4096), default=2048)
    parser.add_argument("--clusters", type=int, choices=range(2, 9), default=4)
    parser.add_argument("--manual-mask")
    parser.add_argument("--assignments")
    parser.add_argument("--materials", default=str(SCRIPT_ROOT / "config" / "materials.json"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--ao-samples", type=int, default=32)
    parser.add_argument("--ao-margin", type=int, default=16)
    parser.add_argument("--normal-strength", type=float, default=1.5)
    parser.add_argument("--normal-method", choices=("blender_bake", "direct"), default="blender_bake")
    parser.add_argument("--normal-convention", choices=("opengl", "directx"), default="opengl")
    parser.add_argument("--normal-margin", type=int, default=24)
    parser.add_argument("--normal-bake-samples", type=int, default=16)
    parser.add_argument("--assetport-package", action="store_true")
    parser.add_argument("--assetport-output")
    parser.add_argument(
        "--assetport-category",
        choices=("env", "wpn", "prop", "char", "veh", "fx"),
        default="env",
    )
    parser.add_argument("--assetport-name")
    parser.add_argument("--basecolor-normal-strength", type=float, default=0.75)
    parser.add_argument("--basecolor-feature-size", type=int, default=12)
    parser.add_argument("--maps-only", action="store_true")
    parser.add_argument("--skip-ao", action="store_true")
    parser.add_argument("--skip-preview", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    args.asset = str(Path(args.asset).resolve())
    if args.output:
        args.output = str(Path(args.output).resolve())
    else:
        asset_path = Path(args.asset)
        root = asset_path.parent
        base_name = f"{asset_path.stem}_PBR_Maps"
        candidate = root / base_name
        index = 1
        while candidate.exists():
            candidate = root / f"{base_name}_{index:03d}"
            index += 1
        args.output = str(candidate.resolve())
    if args.assetport_output:
        args.assetport_output = str(Path(args.assetport_output).resolve())
    return args


def json_dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def set_color_space(image: bpy.types.Image, name: str) -> None:
    try:
        image.colorspace_settings.name = name
    except Exception:
        # Blender builds can expose slightly different OCIO names.
        if name == "Non-Color":
            image.colorspace_settings.name = "Raw"


def image_to_array(image: bpy.types.Image) -> np.ndarray:
    width, height = int(image.size[0]), int(image.size[1])
    if width <= 0 or height <= 0:
        raise PipelineError(f"图像没有有效尺寸：{image.name}")
    result = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(result)
    return result.reshape((height, width, 4))


def array_to_image(name: str, rgba: np.ndarray, color_space: str) -> bpy.types.Image:
    height, width, channels = rgba.shape
    if channels != 4:
        raise ValueError("Expected RGBA array")
    old = bpy.data.images.get(name)
    if old:
        bpy.data.images.remove(old)
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    set_color_space(image, color_space)
    image.pixels.foreach_set(np.clip(rgba, 0.0, 1.0).astype(np.float32).ravel())
    return image


def save_rgba(path: Path, rgba: np.ndarray, color_space: str) -> bpy.types.Image:
    image = array_to_image(f"LMR_{path.stem}", rgba, color_space)
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    return image


def scalar_to_rgba(values: np.ndarray, alpha: np.ndarray | None = None) -> np.ndarray:
    rgba = np.empty((*values.shape, 4), dtype=np.float32)
    rgba[..., 0:3] = values[..., None]
    rgba[..., 3] = 1.0 if alpha is None else alpha
    return rgba


def resize_nearest(array: np.ndarray, height: int, width: int) -> np.ndarray:
    source_h, source_w = array.shape[:2]
    ys = np.minimum((np.arange(height) * source_h / height).astype(np.int32), source_h - 1)
    xs = np.minimum((np.arange(width) * source_w / width).astype(np.int32), source_w - 1)
    return array[ys[:, None], xs[None, :]]


def resize_blender_image(source: bpy.types.Image, resolution: int) -> np.ndarray:
    copy = source.copy()
    try:
        if tuple(copy.size) != (resolution, resolution):
            copy.scale(resolution, resolution)
        return image_to_array(copy)
    finally:
        bpy.data.images.remove(copy)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_asset(path: str) -> list[bpy.types.Object]:
    extension = Path(path).suffix.lower()
    if extension not in {".glb", ".gltf"}:
        raise PipelineError("当前 MVP 仅支持 GLB/GLTF；请先转换其他格式。")
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=path)
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not objects:
        raise PipelineError("资产中没有 Mesh。")
    return objects


def sanitize_assetport_name(value: str) -> str:
    """Return a filename stem compatible with AssetPort's naming parser."""
    # AssetPort reserves the last underscore-delimited token before a texture
    # suffix for a material-slot name. Collapse separators into CamelCase so
    # ordinary names such as ``roof_panel`` cannot be mis-grouped as base
    # ``roof`` + slot ``panel``. Hyphens are also reserved for Atlas kits.
    parts = [part for part in re.split(r"[^\w]+|_+", value, flags=re.UNICODE) if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts)
    return name or "Asset"


def reserve_assetport_name(package_dir: Path, category: str, requested: str) -> str:
    base_name = sanitize_assetport_name(requested)
    candidate = base_name
    index = 1
    while any(
        (package_dir / filename).exists()
        for filename in (
            f"SM_{category}_{candidate}.fbx",
            f"T_{category}_{candidate}_D.png",
            f"T_{category}_{candidate}_N.png",
            f"T_{category}_{candidate}_ORM.png",
        )
    ):
        candidate = f"{base_name}{index:03d}"
        index += 1
    return candidate


def export_assetport_fbx(objects: list[bpy.types.Object], output_path: Path) -> None:
    """Export a triangulated static-mesh FBX with tangents for UE/AssetPort."""
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.fbx(
        filepath=str(output_path),
        check_existing=False,
        use_selection=True,
        object_types={"MESH"},
        global_scale=1.0,
        apply_unit_scale=True,
        use_space_transform=True,
        bake_space_transform=False,
        axis_forward="-Y",
        axis_up="Z",
        use_mesh_modifiers=True,
        mesh_smooth_type="FACE",
        use_tspace=True,
        use_triangles=True,
        use_custom_props=False,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="AUTO",
        embed_textures=False,
        use_metadata=False,
    )


def collect_material_records(objects: list[bpy.types.Object]) -> tuple[list[dict], dict[tuple[str, int], int]]:
    records: list[dict] = []
    lookup: dict[tuple[str, int], int] = {}
    for obj in objects:
        slot_count = max(1, len(obj.material_slots))
        for slot_index in range(slot_count):
            material = obj.material_slots[slot_index].material if slot_index < len(obj.material_slots) else None
            name = material.name if material else f"{obj.name}_default"
            key = (obj.name, slot_index)
            lookup[key] = len(records)
            records.append(
                {
                    "id": len(records),
                    "object": obj.name,
                    "slot": slot_index,
                    "name": name,
                    "suggested_type": infer_material_type(name, material),
                }
            )
    return records, lookup


def infer_material_type(name: str, material: bpy.types.Material | None = None) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower())
    tests = [
        (r"rust|corrosion", "rusted_metal"),
        (r"glass|window|透明|玻璃", "glass"),
        (r"concrete|cement|混凝土|水泥", "concrete"),
        (r"plaster|stucco|灰泥", "plaster"),
        (r"wood|timber|木", "wood"),
        (r"rubber|橡胶", "rubber"),
        (r"plastic|polymer|塑料", "plastic"),
        (r"paint.*metal|metal.*paint|涂漆", "painted_metal"),
        (r"metal|steel|iron|aluminium|aluminum|金属|钢|铁", "bare_metal"),
        (r"paint|coating|lacquer|涂层|油漆", "painted_surface"),
    ]
    for pattern, result in tests:
        if re.search(pattern, normalized):
            return result
    if material and material.use_nodes:
        principled = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if principled and "Metallic" in principled.inputs:
            if float(principled.inputs["Metallic"].default_value) >= 0.65:
                return "bare_metal"
    return "generic_dielectric"


def find_basecolor_images(objects: list[bpy.types.Object]) -> list[bpy.types.Image]:
    direct: list[bpy.types.Image] = []
    fallback: list[bpy.types.Image] = []
    seen = set()
    for obj in objects:
        for slot in obj.material_slots:
            material = slot.material
            if not material or not material.use_nodes:
                continue
            nodes = material.node_tree.nodes
            for node in nodes:
                if node.type == "TEX_IMAGE" and node.image and node.image.name not in seen:
                    fallback.append(node.image)
                    seen.add(node.image.name)
            principled_nodes = [node for node in nodes if node.type == "BSDF_PRINCIPLED"]
            for principled in principled_nodes:
                socket = principled.inputs.get("Base Color")
                if not socket:
                    continue
                for link in socket.links:
                    node = link.from_node
                    if node.type == "TEX_IMAGE" and node.image and node.image not in direct:
                        direct.append(node.image)
    return direct or fallback


def inspect_asset(objects: list[bpy.types.Object], basecolor_images: list[bpy.types.Image]) -> dict:
    vertices = 0
    triangles = 0
    uv_layers = []
    bbox_points = []
    warnings = []
    for obj in objects:
        mesh = obj.data
        mesh.calc_loop_triangles()
        vertices += len(mesh.vertices)
        triangles += len(mesh.loop_triangles)
        uv_layers.append(len(mesh.uv_layers))
        if not mesh.uv_layers:
            raise PipelineError(f"Mesh '{obj.name}' 没有 UV。")
        for corner in obj.bound_box:
            bbox_points.append(obj.matrix_world @ __import__("mathutils").Vector(corner))

    if not basecolor_images:
        raise PipelineError("未找到连接到材质节点的 BaseColor 图像。")
    if len(basecolor_images) > 1:
        warnings.append("检测到多张候选 BaseColor；MVP 将选择像素数最大的一张。")
    if any(count > 1 for count in uv_layers):
        warnings.append("检测到多套 UV；当前输出统一使用每个 Mesh 的活动 UV。")

    mins = [min(point[i] for point in bbox_points) for i in range(3)]
    maxs = [max(point[i] for point in bbox_points) for i in range(3)]
    return {
        "mesh_count": len(objects),
        "vertices": vertices,
        "triangles": triangles,
        "material_slots": sum(max(1, len(obj.material_slots)) for obj in objects),
        "uv": True,
        "uv_layers_per_mesh": uv_layers,
        "basecolor": True,
        "basecolor_images": [
            {"name": image.name, "width": int(image.size[0]), "height": int(image.size[1])}
            for image in basecolor_images
        ],
        "bounding_box": {"min": mins, "max": maxs},
        "warnings": warnings,
    }


def rasterize_uv(
    objects: list[bpy.types.Object],
    material_lookup: dict[tuple[str, int], int],
    resolution: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    ids = np.full((resolution, resolution), UNKNOWN_ID, dtype=np.uint8)
    coverage = np.zeros((resolution, resolution), dtype=bool)
    triangle_edges = np.zeros((resolution, resolution), dtype=bool)
    writes = np.zeros((resolution, resolution), dtype=np.uint16)
    uv_outside = 0
    triangle_count = 0

    for obj in objects:
        mesh = obj.data
        uv_layer = mesh.uv_layers.active
        mesh.calc_loop_triangles()
        for tri in mesh.loop_triangles:
            triangle_count += 1
            uv = np.array([uv_layer.data[index].uv[:] for index in tri.loops], dtype=np.float64)
            if np.any(uv < -1e-5) or np.any(uv > 1.00001):
                uv_outside += 1
            uv = np.clip(uv, 0.0, 1.0)
            points = uv * (resolution - 1)
            x0 = max(0, int(math.floor(np.min(points[:, 0]))))
            x1 = min(resolution - 1, int(math.ceil(np.max(points[:, 0]))))
            y0 = max(0, int(math.floor(np.min(points[:, 1]))))
            y1 = min(resolution - 1, int(math.ceil(np.max(points[:, 1]))))
            if x1 < x0 or y1 < y0:
                continue

            a, b, c = points
            for start, end in ((a, b), (b, c), (c, a)):
                steps = max(2, int(math.ceil(np.max(np.abs(end - start)))) * 2)
                along = np.linspace(0.0, 1.0, steps)
                edge_points = start[None, :] * (1.0 - along[:, None]) + end[None, :] * along[:, None]
                edge_x = np.clip(np.rint(edge_points[:, 0]).astype(int), 0, resolution - 1)
                edge_y = np.clip(np.rint(edge_points[:, 1]).astype(int), 0, resolution - 1)
                triangle_edges[edge_y, edge_x] = True
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(denominator) < 1e-10:
                continue
            yy, xx = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
            px = xx + 0.5
            py = yy + 0.5
            w1 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / denominator
            w2 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / denominator
            w3 = 1.0 - w1 - w2
            inside = (w1 >= -1e-7) & (w2 >= -1e-7) & (w3 >= -1e-7)
            if not np.any(inside):
                continue
            polygon = mesh.polygons[tri.polygon_index]
            slot_index = min(polygon.material_index, max(0, len(obj.material_slots) - 1))
            material_id = material_lookup.get((obj.name, slot_index), 0)
            region_ids = ids[y0 : y1 + 1, x0 : x1 + 1]
            region_coverage = coverage[y0 : y1 + 1, x0 : x1 + 1]
            region_writes = writes[y0 : y1 + 1, x0 : x1 + 1]
            region_ids[inside] = min(material_id, 254)
            region_coverage[inside] = True
            region_writes[inside] += 1

    covered_count = int(np.count_nonzero(coverage))
    multiwrite = int(np.count_nonzero((writes > 1) & coverage))
    stats = {
        "triangle_count": triangle_count,
        "triangles_with_uv_outside_0_1": uv_outside,
        "coverage_ratio": round(covered_count / coverage.size, 6),
        "multiple_write_ratio": round(multiwrite / max(1, covered_count), 6),
        "triangle_edge_ratio": round(
            int(np.count_nonzero(triangle_edges & coverage)) / max(1, covered_count), 6
        ),
        "note": "multiple_write includes shared triangle edges and is only a warning signal, not an exact overlap test",
    }
    return ids, coverage, triangle_edges, stats


def kmeans_material_ids(basecolor: np.ndarray, coverage: np.ndarray, clusters: int) -> tuple[np.ndarray, list[dict]]:
    rgb = basecolor[..., :3]
    coords = np.flatnonzero(coverage.ravel())
    if coords.size == 0:
        raise PipelineError("UV coverage 为空，无法生成材质区域。")
    values = rgb.reshape((-1, 3))[coords]
    rng = np.random.default_rng(20260826)
    if len(values) > 60000:
        sample = values[rng.choice(len(values), 60000, replace=False)]
    else:
        sample = values

    luminance = sample @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    order = np.argsort(luminance)
    positions = np.linspace(0, len(order) - 1, clusters + 2, dtype=int)[1:-1]
    centers = sample[order[positions]].astype(np.float32)
    for _ in range(16):
        distances = np.sum((sample[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        labels = np.argmin(distances, axis=1)
        new_centers = centers.copy()
        for index in range(clusters):
            members = sample[labels == index]
            if len(members):
                new_centers[index] = np.mean(members, axis=0)
        if np.max(np.abs(new_centers - centers)) < 1e-5:
            centers = new_centers
            break
        centers = new_centers

    result = np.full(coverage.shape, UNKNOWN_ID, dtype=np.uint8)
    flat_result = result.ravel()
    chunk_size = 300000
    for start in range(0, len(coords), chunk_size):
        chunk_coords = coords[start : start + chunk_size]
        chunk = rgb.reshape((-1, 3))[chunk_coords]
        distances = np.sum((chunk[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        flat_result[chunk_coords] = np.argmin(distances, axis=1).astype(np.uint8)

    summaries = []
    for index, center in enumerate(centers):
        count = int(np.count_nonzero(result == index))
        summaries.append(
            {
                "id": index,
                "label": f"color_cluster_{index}",
                "mean_rgb_linear": [round(float(value), 5) for value in center],
                "pixel_count": count,
            }
        )
    return result, summaries


def load_manual_mask(path: str, resolution: int, coverage: np.ndarray) -> np.ndarray:
    image = bpy.data.images.load(path, check_existing=False)
    raw = image_to_array(image)
    raw = resize_nearest(raw, resolution, resolution)
    ids = np.rint(np.clip(raw[..., 0], 0.0, 1.0) * 255.0).astype(np.uint8)
    ids[~coverage] = UNKNOWN_ID
    return ids


def load_material_db(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"metallic", "roughness", "variation", "basecolor_detail_weight", "normal_strength"}
    for name, item in data.items():
        missing = required - set(item)
        if missing:
            raise PipelineError(f"材质 '{name}' 缺少字段：{sorted(missing)}")
        for key in required:
            value = float(item[key])
            if not 0.0 <= value <= 1.0:
                raise PipelineError(f"材质 '{name}' 的 {key} 超出 0–1：{value}")
    return data


def build_assignments(
    ids: np.ndarray,
    source: str,
    records: list[dict],
    cluster_summaries: list[dict],
    override_path: str | None,
) -> dict:
    present = sorted(int(value) for value in np.unique(ids) if value != UNKNOWN_ID)
    suggested_by_id = {record["id"]: record["suggested_type"] for record in records}
    fallback_type = records[0]["suggested_type"] if records else "generic_dielectric"
    materials = {}
    for material_id in present:
        if source == "material_slots":
            related = next((record for record in records if record["id"] == material_id), None)
            label = related["name"] if related else f"material_{material_id}"
            suggested = suggested_by_id.get(material_id, fallback_type)
        elif source == "color_clusters":
            related = next((item for item in cluster_summaries if item["id"] == material_id), None)
            label = related["label"] if related else f"cluster_{material_id}"
            suggested = fallback_type
        else:
            label = f"manual_{material_id}"
            suggested = suggested_by_id.get(material_id, fallback_type)
        materials[str(material_id)] = {
            "type": suggested,
            "label": label,
            "pixel_count": int(np.count_nonzero(ids == material_id)),
        }

    if override_path:
        override = json.loads(Path(override_path).read_text(encoding="utf-8"))
        override_materials = override.get("materials", override)
        for key, value in override_materials.items():
            if key not in materials:
                continue
            if isinstance(value, str):
                materials[key]["type"] = value
            elif isinstance(value, dict):
                materials[key].update(value)
    return {"source": source, "materials": materials}


def box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return image.copy()
    padded = np.pad(image, ((radius, radius), (radius, radius)), mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
    diameter = radius * 2 + 1
    total = (
        integral[diameter:, diameter:]
        - integral[:-diameter, diameter:]
        - integral[diameter:, :-diameter]
        + integral[:-diameter, :-diameter]
    )
    return total / float(diameter * diameter)


def erode_mask(mask: np.ndarray, iterations: int) -> np.ndarray:
    result = mask.astype(bool).copy()
    for _ in range(max(0, iterations)):
        if not np.any(result):
            break
        padded = np.pad(result, 1, mode="constant", constant_values=False)
        neighbors = [
            padded[dy : dy + result.shape[0], dx : dx + result.shape[1]]
            for dy in range(3)
            for dx in range(3)
        ]
        result = np.logical_and.reduce(neighbors)
    return result


def dilate_mask(mask: np.ndarray, iterations: int) -> np.ndarray:
    result = mask.astype(bool).copy()
    for _ in range(max(0, iterations)):
        padded = np.pad(result, 1, mode="constant", constant_values=False)
        neighbors = [
            padded[dy : dy + result.shape[0], dx : dx + result.shape[1]]
            for dy in range(3)
            for dx in range(3)
        ]
        result = np.logical_or.reduce(neighbors)
    return result


def soft_inner_fade(mask: np.ndarray, radius: int) -> np.ndarray:
    """0 at a mask boundary, rising smoothly to 1 toward the interior."""
    radius = max(1, radius)
    weight = np.zeros(mask.shape, dtype=np.float32)
    current = mask.astype(bool).copy()
    for step in range(radius):
        inner = erode_mask(current, 1)
        ring = current & ~inner
        t = step / float(radius)
        weight[ring] = t * t * (3.0 - 2.0 * t)
        current = inner
        if not np.any(current):
            break
    weight[current] = 1.0
    return weight


def soft_exclusion_fade(excluded: np.ndarray, radius: int) -> np.ndarray:
    """0 on excluded pixels, rising to 1 away from them."""
    radius = max(1, radius)
    weight = np.ones(excluded.shape, dtype=np.float32)
    current = excluded.astype(bool).copy()
    weight[current] = 0.0
    for step in range(1, radius + 1):
        expanded = dilate_mask(current, 1)
        ring = expanded & ~current
        t = step / float(radius)
        weight[ring] = t * t * (3.0 - 2.0 * t)
        current = expanded
    return weight


def masked_box_blur(image: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
    weights = box_blur(mask.astype(np.float32), radius)
    values = box_blur(image * mask, radius)
    result = image.copy()
    valid = weights > 1e-5
    result[valid] = values[valid] / weights[valid]
    return result


def extreme_filter(image: np.ndarray, radius: int, mode: str) -> np.ndarray:
    if radius <= 0:
        return image.copy()
    operation = np.max if mode == "max" else np.min
    kernel = radius * 2 + 1
    horizontal = np.lib.stride_tricks.sliding_window_view(
        np.pad(image, ((0, 0), (radius, radius)), mode="edge"), kernel, axis=1
    )
    horizontal = operation(horizontal, axis=-1)
    vertical = np.lib.stride_tricks.sliding_window_view(
        np.pad(horizontal, ((radius, radius), (0, 0)), mode="edge"), kernel, axis=0
    )
    return operation(vertical, axis=-1)


def extract_dark_grooves(
    luminance: np.ndarray,
    coverage: np.ndarray,
    feature_size_at_2k: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a negative height map containing local dark-line features only.

    Broad illumination gradients and bright paint patterns are ignored. A UV
    boundary guard prevents island borders and atlas padding from turning into
    false relief.
    """
    height, width = luminance.shape
    scale = width / 2048.0
    feature_radius = max(2, int(round(max(2, feature_size_at_2k) * scale)))
    guard_radius = max(2, int(round(4 * scale)))
    safe = erode_mask(coverage, guard_radius)
    if np.count_nonzero(safe) < np.count_nonzero(coverage) * 0.2:
        safe = erode_mask(coverage, 1)

    small = masked_box_blur(luminance, coverage, 1)
    # Grayscale closing fills narrow dark gaps but preserves bright markings,
    # avoiding the false dark halos produced by ordinary local averaging.
    local_reference = extreme_filter(
        extreme_filter(small, feature_radius, "max"), feature_radius, "min"
    )
    darkness = np.maximum(local_reference - small, 0.0)
    samples = darkness[safe]
    if samples.size == 0:
        return np.zeros_like(luminance, dtype=np.float32), safe

    # Discard small tonal drift/noise and keep locally prominent dark grooves.
    noise_floor = float(np.percentile(samples, 62))
    high = float(np.percentile(samples, 96))
    denominator = max(high - noise_floor, 1e-5)
    groove = np.clip((darkness - noise_floor) / denominator, 0.0, 1.0)
    groove = np.clip(box_blur(groove, max(1, feature_radius // 5)), 0.0, 1.0)
    # Keep an explicitly flat guard band inside each UV island. This prevents
    # the morphology kernel from turning atlas padding into a false groove.
    groove[~safe] = 0.0
    return -groove.astype(np.float32), safe


def structure_reliability(
    coverage: np.ndarray, triangle_edges: np.ndarray | None
) -> float:
    covered = int(np.count_nonzero(coverage))
    if covered == 0:
        return 0.0
    coverage_ratio = covered / coverage.size
    coverage_score = float(np.clip((coverage_ratio - 0.03) / 0.27, 0.15, 1.0))
    if triangle_edges is None:
        return coverage_score
    edge_ratio = int(np.count_nonzero(triangle_edges & coverage)) / covered
    edge_score = float(np.clip((0.65 - edge_ratio) / 0.45, 0.15, 1.0))
    return min(coverage_score, edge_score)


def smooth_noise(
    height: int, width: int, seed: int = 20260826, cell_size: int = 48
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    grid_h = max(8, height // max(2, cell_size))
    grid_w = max(8, width // max(2, cell_size))
    grid = rng.normal(0.0, 1.0, (grid_h + 1, grid_w + 1)).astype(np.float32)
    yy = np.linspace(0, grid_h - 1e-5, height)
    xx = np.linspace(0, grid_w - 1e-5, width)
    y0 = np.floor(yy).astype(int)
    x0 = np.floor(xx).astype(int)
    y1 = np.minimum(y0 + 1, grid_h)
    x1 = np.minimum(x0 + 1, grid_w)
    fy = (yy - y0)[:, None]
    fx = (xx - x0)[None, :]
    top = grid[y0[:, None], x0[None, :]] * (1.0 - fx) + grid[y0[:, None], x1[None, :]] * fx
    bottom = grid[y1[:, None], x0[None, :]] * (1.0 - fx) + grid[y1[:, None], x1[None, :]] * fx
    value = top * (1.0 - fy) + bottom * fy
    value -= float(np.mean(value))
    scale = float(np.std(value))
    return value / max(scale, 1e-6)


def generate_pbr(
    basecolor: np.ndarray,
    ids: np.ndarray,
    coverage: np.ndarray,
    assignments: dict,
    material_db: dict,
    normal_strength_multiplier: float,
    basecolor_normal_strength: float,
    basecolor_feature_size: int,
    triangle_edges: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    height, width = ids.shape
    roughness = np.ones((height, width), dtype=np.float32)
    metallic = np.zeros((height, width), dtype=np.float32)
    strength = np.zeros((height, width), dtype=np.float32)

    luminance = basecolor[..., :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    low = box_blur(luminance, max(2, width // 256))
    high = luminance - low
    valid_high = np.abs(high[coverage])
    high_scale = float(np.percentile(valid_high, 95)) if valid_high.size else 1.0
    high = np.clip(high / max(high_scale, 1e-5), -1.0, 1.0)
    noise = np.clip(smooth_noise(height, width, cell_size=48), -2.0, 2.0) / 2.0

    for id_text, assignment in assignments["materials"].items():
        material_id = int(id_text)
        type_name = assignment.get("type", "generic_dielectric")
        if type_name not in material_db:
            raise PipelineError(f"未知材质类型：{type_name}")
        params = material_db[type_name]
        mask = ids == material_id
        roughness[mask] = (
            float(params["roughness"])
            + noise[mask] * float(params["variation"])
            + high[mask] * float(params["basecolor_detail_weight"])
        )
        metallic[mask] = float(params["metallic"])
        strength[mask] = float(params["normal_strength"])

    roughness = np.clip(roughness, 0.0, 1.0)
    metallic = np.clip(metallic, 0.0, 1.0)
    roughness[~coverage] = 1.0
    metallic[~coverage] = 0.0

    # A multi-scale height field gives actual micro-surface variation. Gradients
    # are RMS-normalized so the visual strength does not disappear at 2K/4K.
    fine = smooth_noise(height, width, seed=20260827, cell_size=6)
    medium = smooth_noise(height, width, seed=20260828, cell_size=20)
    detail_height = fine * 0.62 + medium * 0.30 + high * 0.08
    micro_y, micro_x = np.gradient(detail_height)
    valid_gradient = (
        micro_x[coverage] ** 2 + micro_y[coverage] ** 2
        if np.any(coverage)
        else np.array([1.0])
    )
    gradient_rms = math.sqrt(max(float(np.mean(valid_gradient)), 1e-8))
    slope = strength * max(0.0, normal_strength_multiplier) * 0.35
    nx = -(micro_x / gradient_rms) * slope
    ny = -(micro_y / gradient_rms) * slope

    # Infer broad grooves from local BaseColor contrast. Dark lines become
    # depressions. This is intentionally optional: printed patterns and baked
    # lighting can otherwise be mistaken for geometry.
    reliability = structure_reliability(coverage, triangle_edges)
    structure_strength = max(0.0, float(basecolor_normal_strength)) * reliability
    structure = np.zeros((height, width), dtype=np.float32)
    if structure_strength > 0.0 and np.any(coverage):
        structure, safe = extract_dark_grooves(
            luminance,
            coverage,
            basecolor_feature_size,
        )
        # Fade only at actual UV island borders. Do not attenuate along every
        # rasterized triangle edge: doing so creates an artificial height ramp
        # beside each diagonal and is itself visible in the baked normal map.
        # Triangle density is already handled by structure_reliability().
        uv_fade_radius = max(3, int(round(12 * width / 2048.0)))
        confidence_fade = soft_inner_fade(coverage, uv_fade_radius)
        structure *= confidence_fade
        safe = coverage & (confidence_fade > 0.05)
        structure_y, structure_x = np.gradient(structure)
        gradient_length = np.sqrt(structure_x[safe] ** 2 + structure_y[safe] ** 2)
        meaningful = gradient_length[gradient_length > 1e-6]
        structure_scale = float(np.percentile(meaningful, 90)) if meaningful.size else 1.0
        structure_x = np.clip(structure_x / max(structure_scale, 1e-5), -2.0, 2.0)
        structure_y = np.clip(structure_y / max(structure_scale, 1e-5), -2.0, 2.0)
        structure_x[~safe] = 0.0
        structure_y[~safe] = 0.0
        nx -= structure_x * structure_strength * 0.35
        ny -= structure_y * structure_strength * 0.35
    nz = np.ones_like(nx)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    normal = np.empty((height, width, 4), dtype=np.float32)
    normal[..., 0] = nx / length * 0.5 + 0.5
    normal[..., 1] = ny / length * 0.5 + 0.5
    normal[..., 2] = nz / length * 0.5 + 0.5
    normal[..., 3] = 1.0
    normal[~coverage, :3] = (0.5, 0.5, 1.0)
    structure_height = np.clip(
        0.5 + structure * 0.35 * max(0.0, float(basecolor_normal_strength)) * reliability,
        0.0,
        1.0,
    )
    structure_height[~coverage] = 0.5
    material_strength = np.clip(strength / 0.35, 0.0, 1.0)
    material_strength[~coverage] = 0.0
    return roughness, metallic, normal, structure_height, material_strength


def configure_cycles(device: str, samples: int, warnings: list[str]) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    if device != "cuda":
        scene.cycles.device = "CPU"
        return
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "CUDA"
        preferences.get_devices()
        enabled = 0
        for item in preferences.devices:
            item.use = item.type == "CUDA"
            enabled += int(item.use)
        if not enabled:
            raise RuntimeError("No CUDA device exposed to Blender")
        scene.cycles.device = "GPU"
    except Exception as exc:
        warnings.append(f"CUDA 初始化失败，已回退 CPU：{exc}")
        scene.cycles.device = "CPU"


def ensure_bake_material(obj: bpy.types.Object) -> list[bpy.types.Material]:
    if not obj.data.materials:
        material = bpy.data.materials.new(f"LMR_Bake_{obj.name}")
        material.use_nodes = True
        obj.data.materials.append(material)
    materials = []
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            material = bpy.data.materials.new(f"LMR_Bake_{obj.name}_{len(materials)}")
            material.use_nodes = True
            slot.material = material
        if not material.use_nodes:
            material.use_nodes = True
        materials.append(material)
    return materials


def build_height_bake_material(
    structure_image: bpy.types.Image,
    strength_image: bpy.types.Image,
    target_image: bpy.types.Image,
    normal_strength: float,
) -> bpy.types.Material:
    material = bpy.data.materials.new("LMR_SurfaceDomain_NormalBake")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    principled.inputs["Roughness"].default_value = 0.5
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    structure_tex = nodes.new("ShaderNodeTexImage")
    structure_tex.name = "LMR_StructureHeight"
    structure_tex.image = structure_image
    structure_tex.interpolation = "Linear"
    structure_tex.extension = "EXTEND"

    strength_tex = nodes.new("ShaderNodeTexImage")
    strength_tex.name = "LMR_MaterialNormalStrength"
    strength_tex.image = strength_image
    strength_tex.interpolation = "Linear"
    strength_tex.extension = "EXTEND"

    texcoord = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "3D"
    noise.inputs["Scale"].default_value = 85.0
    noise.inputs["Detail"].default_value = 3.0
    noise.inputs["Roughness"].default_value = 0.62
    links.new(texcoord.outputs["Object"], noise.inputs["Vector"])

    noise_center = nodes.new("ShaderNodeMath")
    noise_center.operation = "SUBTRACT"
    noise_center.inputs[1].default_value = 0.5
    links.new(noise.outputs["Fac"], noise_center.inputs[0])
    noise_amplitude = nodes.new("ShaderNodeMath")
    noise_amplitude.operation = "MULTIPLY"
    noise_amplitude.inputs[1].default_value = 0.08
    links.new(noise_center.outputs[0], noise_amplitude.inputs[0])

    combined_height = nodes.new("ShaderNodeMath")
    combined_height.operation = "ADD"
    links.new(structure_tex.outputs["Color"], combined_height.inputs[0])
    links.new(noise_amplitude.outputs[0], combined_height.inputs[1])

    strength_scale = nodes.new("ShaderNodeMath")
    strength_scale.operation = "MULTIPLY"
    strength_scale.inputs[1].default_value = max(0.0, min(float(normal_strength), 2.0))
    links.new(strength_tex.outputs["Color"], strength_scale.inputs[0])

    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Distance"].default_value = 0.10
    links.new(strength_scale.outputs[0], bump.inputs["Strength"])
    links.new(combined_height.outputs[0], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])

    target = nodes.new("ShaderNodeTexImage")
    target.name = "LMR_NORMAL_BAKE_TARGET"
    target.image = target_image
    for node in nodes:
        node.select = False
    target.select = True
    nodes.active = target
    return material


def bake_detail_normal(
    objects: list[bpy.types.Object],
    structure_height: np.ndarray,
    material_strength: np.ndarray,
    coverage: np.ndarray,
    resolution: int,
    output_path: Path,
    device: str,
    samples: int,
    margin: int,
    normal_strength: float,
    convention: str,
    warnings: list[str],
) -> np.ndarray:
    configure_cycles(device, samples, warnings)
    structure_image = array_to_image(
        "LMR_Internal_StructureHeight",
        scalar_to_rgba(structure_height),
        "Non-Color",
    )
    strength_image = array_to_image(
        "LMR_Internal_NormalStrength",
        scalar_to_rgba(material_strength),
        "Non-Color",
    )
    flat = np.empty((resolution, resolution, 4), dtype=np.float32)
    flat[..., 0] = 0.5
    flat[..., 1] = 0.5
    flat[..., 2] = 1.0
    flat[..., 3] = 1.0
    target_image = array_to_image("LMR_Normal_Bake", flat, "Non-Color")
    material = build_height_bake_material(
        structure_image,
        strength_image,
        target_image,
        normal_strength,
    )

    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(material)

    for obj in objects:
        for candidate in bpy.context.selected_objects:
            candidate.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        nodes = material.node_tree.nodes
        nodes.active = nodes.get("LMR_NORMAL_BAKE_TARGET")
        bpy.ops.object.bake(
            type="NORMAL",
            normal_space="TANGENT",
            use_clear=False,
            margin=max(0, margin),
        )

    normal = image_to_array(target_image)
    keep = dilate_mask(coverage, max(1, margin))
    normal[~keep, :3] = (0.5, 0.5, 1.0)
    vectors = normal[..., :3] * 2.0 - 1.0
    lengths = np.linalg.norm(vectors, axis=2, keepdims=True)
    vectors /= np.maximum(lengths, 1e-6)
    normal[..., :3] = vectors * 0.5 + 0.5
    normal[..., 3] = 1.0
    if convention == "directx":
        normal[..., 1] = 1.0 - normal[..., 1]
    save_rgba(output_path, normal, "Non-Color")
    return normal


def bake_ao(
    objects: list[bpy.types.Object],
    resolution: int,
    output_path: Path,
    device: str,
    samples: int,
    margin: int,
    warnings: list[str],
) -> np.ndarray:
    configure_cycles(device, samples, warnings)
    image = bpy.data.images.new("LMR_AO_Bake", width=resolution, height=resolution, alpha=True)
    set_color_space(image, "Non-Color")
    image.generated_color = (1.0, 1.0, 1.0, 1.0)
    temp_nodes = []
    first = True
    try:
        for obj in objects:
            for candidate in bpy.context.selected_objects:
                candidate.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            for material in ensure_bake_material(obj):
                nodes = material.node_tree.nodes
                for node in nodes:
                    node.select = False
                target = nodes.new("ShaderNodeTexImage")
                target.name = "LMR_TEMP_AO_TARGET"
                target.image = image
                target.select = True
                nodes.active = target
                temp_nodes.append((nodes, target))
            bpy.ops.object.bake(type="AO", use_clear=first, margin=margin)
            first = False
        array = image_to_array(image)
        image.filepath_raw = str(output_path)
        image.file_format = "PNG"
        image.save()
        return array[..., 0].copy()
    finally:
        for nodes, node in temp_nodes:
            if node.name in nodes:
                nodes.remove(node)


def save_id_image(path: Path, ids: np.ndarray) -> None:
    normalized = ids.astype(np.float32) / 255.0
    save_rgba(path, scalar_to_rgba(normalized), "Non-Color")


def create_preview_material(
    objects: list[bpy.types.Object], output_dir: Path, warnings: list[str], assignments: dict
) -> None:
    material = bpy.data.materials.new("LMR_PBR_Preview")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (700, 0)
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (430, 0)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    base_node = nodes.new("ShaderNodeTexImage")
    base_node.label = "Generated BaseColor"
    base_node.location = (-500, 180)
    base_node.image = bpy.data.images.load(str(output_dir / "basecolor.png"), check_existing=False)
    set_color_space(base_node.image, "sRGB")
    links.new(base_node.outputs["Color"], principled.inputs["Base Color"])

    orm_node = nodes.new("ShaderNodeTexImage")
    orm_node.label = "ORM (R=AO G=Roughness B=Metallic)"
    orm_node.location = (-500, -80)
    orm_node.image = bpy.data.images.load(str(output_dir / "ORM.png"), check_existing=False)
    set_color_space(orm_node.image, "Non-Color")
    separate = nodes.new("ShaderNodeSeparateColor")
    separate.location = (-220, -80)
    links.new(orm_node.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs["Green"], principled.inputs["Roughness"])
    links.new(separate.outputs["Blue"], principled.inputs["Metallic"])

    normal_tex = nodes.new("ShaderNodeTexImage")
    normal_tex.label = "Generated Detail Normal"
    normal_tex.location = (-500, -350)
    normal_tex.image = bpy.data.images.load(str(output_dir / "detail_normal.png"), check_existing=False)
    set_color_space(normal_tex.image, "Non-Color")
    normal_map = nodes.new("ShaderNodeNormalMap")
    normal_map.location = (-200, -330)
    links.new(normal_tex.outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])

    for obj in objects:
        if not obj.data.materials:
            obj.data.materials.append(material)
        else:
            for index in range(len(obj.data.materials)):
                obj.data.materials[index] = material

    if any(item.get("type") == "glass" for item in assignments["materials"].values()):
        warnings.append("预览材质不处理透明混合。")

    render_studio_preview(objects, output_dir / "preview.png")
    preview_path = output_dir / "material_preview.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(preview_path))


def render_studio_preview(objects: list[bpy.types.Object], output_path: Path) -> None:
    from mathutils import Vector

    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    minimum = Vector(tuple(min(point[index] for point in points) for index in range(3)))
    maximum = Vector(tuple(max(point[index] for point in points) for index in range(3)))
    center = (minimum + maximum) * 0.5
    extent = maximum - minimum
    radius = max(float(extent.length) * 0.5, 0.1)

    camera_data = bpy.data.cameras.new("LMR_Preview_Camera")
    camera = bpy.data.objects.new("LMR_Preview_Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = center + Vector((1.5, -2.2, 1.25)).normalized() * radius * 2.8
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.lens = 55
    bpy.context.scene.camera = camera

    def add_area(name: str, offset: tuple[float, float, float], energy: float, size: float) -> None:
        light_data = bpy.data.lights.new(name, type="AREA")
        light_data.energy = energy
        light_data.shape = "DISK"
        light_data.size = size
        light = bpy.data.objects.new(name, light_data)
        bpy.context.scene.collection.objects.link(light)
        light.location = center + Vector(offset).normalized() * radius * 2.5
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()

    add_area("LMR_Key", (1.5, -1.2, 2.0), 900.0 * radius, radius * 1.4)
    add_area("LMR_Fill", (-1.8, -0.5, 0.7), 500.0 * radius, radius * 1.8)
    add_area("LMR_Rim", (0.2, 1.8, 1.5), 700.0 * radius, radius * 1.2)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.render.filepath = str(output_path)
    scene.world.color = (0.035, 0.035, 0.035)
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass
    bpy.ops.render.render(write_still=True)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    debug_dir = output_dir / "debug"
    if args.debug and not args.maps_only:
        debug_dir.mkdir(parents=True, exist_ok=True)

    timer = StageTimer()
    warnings: list[str] = []
    objects = import_asset(args.asset)
    basecolor_images = find_basecolor_images(objects)
    asset_info = inspect_asset(objects, basecolor_images)
    warnings.extend(asset_info["warnings"])
    timer.mark("load_and_inspect")

    resolution = args.resolution
    base_source = max(basecolor_images, key=lambda image: int(image.size[0]) * int(image.size[1]))
    basecolor = resize_blender_image(base_source, resolution)
    save_rgba(output_dir / "basecolor.png", basecolor, "sRGB")
    timer.mark("basecolor")

    assetport_package_dir = None
    assetport_base_name = None
    if args.assetport_package:
        assetport_package_dir = (
            Path(args.assetport_output)
            if args.assetport_output
            else output_dir / "AssetPort_Import"
        )
        assetport_package_dir.mkdir(parents=True, exist_ok=True)
        requested_name = args.assetport_name or Path(args.asset).stem
        assetport_base_name = reserve_assetport_name(
            assetport_package_dir,
            args.assetport_category,
            requested_name,
        )
        export_assetport_fbx(
            objects,
            assetport_package_dir
            / f"SM_{args.assetport_category}_{assetport_base_name}.fbx",
        )
        timer.mark("assetport_mesh")

    records, material_lookup = collect_material_records(objects)
    slot_ids, coverage, triangle_edges, uv_stats = rasterize_uv(objects, material_lookup, resolution)
    asset_info["uv_analysis"] = uv_stats
    basecolor_structure_reliability = structure_reliability(coverage, triangle_edges)
    asset_info["basecolor_structure_reliability"] = round(basecolor_structure_reliability, 4)
    if uv_stats["triangles_with_uv_outside_0_1"]:
        warnings.append("部分 UV 超出 0–1；MVP 已将其裁切，Repeat/Mirror 结果可能不正确。")
    if uv_stats["multiple_write_ratio"] > 0.02:
        warnings.append("UV 存在较高的多次写入比例，可能有重叠 UV。")
    if args.basecolor_normal_strength > 0.0 and basecolor_structure_reliability < 0.5:
        warnings.append(
            "UV atlas 覆盖率较低或三角边界过密，BaseColor 结构法线已自动降权。"
        )

    cluster_summaries: list[dict] = []
    if args.manual_mask:
        material_ids = load_manual_mask(args.manual_mask, resolution, coverage)
        mask_source = "manual_mask"
    elif len(records) > 1:
        material_ids = slot_ids
        mask_source = "material_slots"
    else:
        material_ids, cluster_summaries = kmeans_material_ids(basecolor, coverage, args.clusters)
        mask_source = "color_clusters"
    if not args.maps_only:
        save_id_image(output_dir / "material_id.png", material_ids)
    if args.debug and not args.maps_only:
        save_rgba(debug_dir / "uv_coverage.png", scalar_to_rgba(coverage.astype(np.float32)), "Non-Color")
        save_rgba(
            debug_dir / "triangle_edges.png",
            scalar_to_rgba(triangle_edges.astype(np.float32)),
            "Non-Color",
        )
    timer.mark("material_regions")

    material_db = load_material_db(args.materials)
    assignments = build_assignments(
        material_ids,
        mask_source,
        records,
        cluster_summaries,
        args.assignments,
    )
    for item in assignments["materials"].values():
        if item["type"] not in material_db:
            raise PipelineError(f"Assignments 使用了未定义材质类型：{item['type']}")
    if not args.maps_only:
        json_dump(output_dir / "material_assignments.json", assignments)

    roughness, metallic, direct_normal, structure_height, material_normal_strength = generate_pbr(
        basecolor,
        material_ids,
        coverage,
        assignments,
        material_db,
        args.normal_strength,
        args.basecolor_normal_strength,
        args.basecolor_feature_size,
        triangle_edges,
    )
    save_rgba(output_dir / "roughness.png", scalar_to_rgba(roughness), "Non-Color")
    save_rgba(output_dir / "metallic.png", scalar_to_rgba(metallic), "Non-Color")
    if args.debug and not args.maps_only:
        save_rgba(
            debug_dir / "basecolor_structure_height.png",
            scalar_to_rgba(structure_height),
            "Non-Color",
        )
        save_rgba(
            debug_dir / "material_normal_strength.png",
            scalar_to_rgba(material_normal_strength),
            "Non-Color",
        )
    timer.mark("pbr_parameters")

    if args.normal_method == "blender_bake":
        try:
            normal = bake_detail_normal(
                objects,
                structure_height,
                material_normal_strength,
                coverage,
                resolution,
                output_dir / "detail_normal.png",
                args.device,
                args.normal_bake_samples,
                args.normal_margin,
                args.normal_strength,
                args.normal_convention,
                warnings,
            )
        except Exception as exc:
            warnings.append(f"Blender Normal Bake 失败，已回退直接法线：{exc}")
            normal = direct_normal
            if args.normal_convention == "directx":
                normal[..., 1] = 1.0 - normal[..., 1]
            save_rgba(output_dir / "detail_normal.png", normal, "Non-Color")
    else:
        normal = direct_normal
        if args.normal_convention == "directx":
            normal[..., 1] = 1.0 - normal[..., 1]
        save_rgba(output_dir / "detail_normal.png", normal, "Non-Color")
    timer.mark("normal_bake")

    if args.skip_ao:
        ao = np.ones((resolution, resolution), dtype=np.float32)
        warnings.append("AO 被 --skip-ao 跳过，使用全白 AO。")
    else:
        ao = bake_ao(
            objects,
            resolution,
            output_dir / "ao.png",
            args.device,
            args.ao_samples,
            args.ao_margin,
            warnings,
        )
    ao[~coverage] = 1.0
    ao = np.clip(ao, 0.0, 1.0)
    save_rgba(output_dir / "ao.png", scalar_to_rgba(ao), "Non-Color")
    timer.mark("ao_bake")

    orm = np.empty((resolution, resolution, 4), dtype=np.float32)
    orm[..., 0] = ao
    orm[..., 1] = roughness
    orm[..., 2] = metallic
    orm[..., 3] = 1.0
    save_rgba(output_dir / "ORM.png", orm, "Non-Color")
    timer.mark("orm_pack")

    if assetport_package_dir is not None and assetport_base_name is not None:
        prefix = f"T_{args.assetport_category}_{assetport_base_name}"
        shutil.copyfile(
            output_dir / "basecolor.png",
            assetport_package_dir / f"{prefix}_D.png",
        )
        shutil.copyfile(
            output_dir / "ORM.png",
            assetport_package_dir / f"{prefix}_ORM.png",
        )
        ue_normal = normal.copy()
        if args.normal_convention == "opengl":
            ue_normal[..., 1] = 1.0 - ue_normal[..., 1]
        save_rgba(
            assetport_package_dir / f"{prefix}_N.png",
            ue_normal,
            "Non-Color",
        )
        timer.mark("assetport_textures")

    asset_info["asset"] = Path(args.asset).name
    asset_info["resolution"] = resolution
    asset_info["warnings"] = warnings
    material_metadata = {
        "asset": Path(args.asset).stem,
        "resolution": resolution,
        "region_source": mask_source,
        "normal_strength_multiplier": args.normal_strength,
        "basecolor_normal_strength": args.basecolor_normal_strength,
        "basecolor_feature_size": args.basecolor_feature_size,
        "basecolor_structure_reliability": round(basecolor_structure_reliability, 4),
        "normal_method": args.normal_method,
        "normal_convention": args.normal_convention,
        "assetport_package": (
            {
                "directory": str(assetport_package_dir),
                "base_name": assetport_base_name,
                "category": args.assetport_category,
                "normal_convention": "directx",
            }
            if assetport_package_dir is not None
            else None
        ),
        "materials": [
            {"id": int(key), **value}
            for key, value in sorted(assignments["materials"].items(), key=lambda pair: int(pair[0]))
        ],
        "textures": {
            "basecolor": "basecolor.png",
            "ao": "ao.png",
            "roughness": "roughness.png",
            "metallic": "metallic.png",
            "detail_normal": "detail_normal.png",
            "orm": "ORM.png",
            "material_id": "material_id.png",
        },
        "warnings": warnings,
    }
    if not args.maps_only:
        json_dump(output_dir / "asset_info.json", asset_info)
        json_dump(output_dir / "material.json", material_metadata)
    timer.mark("metadata")

    if args.skip_preview or args.maps_only:
        timer.mark("preview_skipped")
    else:
        create_preview_material(objects, output_dir, warnings, assignments)
        timer.mark("preview_blend")
    timer.finish()
    if not args.maps_only:
        json_dump(output_dir / "timings.json", timer.values)
        # Re-write warnings possibly added by preview creation.
        asset_info["warnings"] = warnings
        material_metadata["warnings"] = warnings
        json_dump(output_dir / "asset_info.json", asset_info)
        json_dump(output_dir / "material.json", material_metadata)

    print(f"[LMR] Done: {output_dir}")
    for warning in warnings:
        print(f"[LMR] WARNING: {warning}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[LMR] ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
