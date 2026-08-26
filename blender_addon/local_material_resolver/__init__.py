bl_info = {
    "name": "Local Material Resolver",
    "author": "Local Material Resolver",
    "version": (0, 7, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > PBR Resolver",
    "description": "Generate AO, Roughness, Metallic, Detail Normal and ORM from a textured GLB",
    "category": "Material",
}

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup


_job = None


def _redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _unique_output_directory(root: Path, base_name: str) -> Path:
    candidate = root / base_name
    index = 1
    while candidate.exists():
        candidate = root / f"{base_name}_{index:03d}"
        index += 1
    return candidate


def _launch_next_asset(job):
    asset = Path(job["pending_assets"].pop(0))
    suffix = "_PBR_Maps" if job["maps_only"] else "_PBR"
    output_root = Path(job["output_root"])
    output_dir = _unique_output_directory(output_root, f"{asset.stem}{suffix}")
    output_dir.mkdir(parents=True, exist_ok=True)

    temporary_log = bool(job["maps_only"])
    if temporary_log:
        handle, temporary_path = tempfile.mkstemp(prefix=f"LMR_{asset.stem}_", suffix=".log")
        os.close(handle)
        log_path = Path(temporary_path)
    else:
        log_path = output_dir / "processing.log"
    log_handle = log_path.open("w", encoding="utf-8")

    command = [
        bpy.app.binary_path,
        "--background",
        "--factory-startup",
        "--python",
        job["worker"],
        "--",
        str(asset),
        "--output",
        str(output_dir),
        *job["common_args"],
    ]
    if job["assetport_package"]:
        package_dir = (
            Path(job["assetport_output_root"])
            if job["batch"]
            else output_dir / "AssetPort_Import"
        )
        package_dir.mkdir(parents=True, exist_ok=True)
        command.extend(
            (
                "--assetport-package",
                "--assetport-output",
                str(package_dir),
                "--assetport-category",
                job["assetport_category"],
            )
        )
        if not job["batch"] and job["assetport_name"]:
            command.extend(("--assetport-name", job["assetport_name"]))
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            cwd=job["addon_dir"],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
    except Exception:
        log_handle.close()
        raise

    job.update(
        {
            "process": process,
            "started": time.time(),
            "current_asset": str(asset),
            "output_dir": str(output_dir),
            "log_handle": log_handle,
            "log_path": str(log_path),
            "temporary_log": temporary_log,
            "current_index": job["total"] - len(job["pending_assets"]),
        }
    )


def _finish_current_asset(job, code: int) -> None:
    job["log_handle"].close()
    output_dir = Path(job["output_dir"])
    log_path = Path(job["log_path"])
    if job["temporary_log"]:
        if code == 0:
            try:
                log_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            error_log = output_dir / ("processing_cancelled.log" if job["cancelled"] else "processing_error.log")
            try:
                shutil.copyfile(log_path, error_log)
                log_path.unlink(missing_ok=True)
            except OSError:
                pass
    if code == 0:
        job["completed_outputs"].append(str(output_dir))
    elif not job["cancelled"]:
        job["failures"].append(
            {"asset": job["current_asset"], "output": str(output_dir), "exit_code": code}
        )


def _poll_job():
    global _job
    if _job is None:
        return None
    process = _job["process"]
    code = process.poll()
    scene = bpy.context.scene
    settings = getattr(scene, "lmr_settings", None) if scene else None
    if code is None:
        if settings:
            elapsed = int(time.time() - _job["started"])
            if _job["batch"]:
                asset_name = Path(_job["current_asset"]).name
                settings.status = (
                    f"批量处理中 {_job['current_index']}/{_job['total']}："
                    f"{asset_name}（{elapsed} 秒）"
                )
            else:
                settings.status = f"正在生成 PBR… {elapsed} 秒"
        _redraw()
        return 0.5

    _finish_current_asset(_job, code)
    if _job["cancelled"]:
        if settings:
            settings.status = f"已停止：完成 {len(_job['completed_outputs'])}/{_job['total']}"
            settings.last_output = _job["result_root"]
        _job = None
        _redraw()
        return None

    if _job["pending_assets"]:
        try:
            _launch_next_asset(_job)
        except Exception as exc:
            _job["failures"].append({"asset": "启动后台进程", "error": str(exc)})
            if settings:
                settings.status = f"批量启动失败：{exc}"
            _job = None
            _redraw()
            return None
        _redraw()
        return 0.5

    result_root = _job["result_root"]
    auto_open = _job["auto_open"]
    failures = len(_job["failures"])
    completed = len(_job["completed_outputs"])
    if settings:
        settings.last_output = result_root
        if _job["batch"]:
            settings.status = f"批量完成：成功 {completed}，失败 {failures}"
        elif failures:
            settings.status = f"处理失败，请查看输出目录中的日志"
        else:
            settings.status = f"完成：{result_root}"
    if auto_open and os.name == "nt":
        try:
            os.startfile(result_root)
        except OSError:
            pass
    _job = None
    _redraw()
    return None


class LMR_Settings(PropertyGroup):
    input_mode: EnumProperty(
        name="处理模式",
        items=[
            ("single", "单个资产", "处理一个 GLB/GLTF"),
            ("batch", "批量文件夹", "处理文件夹中的全部 GLB/GLTF"),
        ],
        default="single",
    )
    asset_path: StringProperty(
        name="输入 GLB",
        subtype="FILE_PATH",
        description="选择带 Mesh、UV 和 BaseColor 的 GLB/GLTF",
    )
    batch_input_dir: StringProperty(
        name="输入文件夹",
        subtype="DIR_PATH",
        description="批量扫描此文件夹中的 GLB/GLTF",
    )
    batch_recursive: BoolProperty(
        name="包含子文件夹",
        description="递归扫描输入文件夹下的所有子文件夹",
        default=False,
    )
    output_dir: StringProperty(
        name="输出根目录",
        subtype="DIR_PATH",
        description="留空时放在资产旁边；每次运行都会创建新的编号文件夹",
    )
    resolution: EnumProperty(
        name="纹理尺寸",
        items=[
            ("512", "512", "快速测试"),
            ("1024", "1024", "较快预览"),
            ("2048", "2048", "推荐"),
            ("4096", "4096", "高分辨率，耗时与显存占用较高"),
        ],
        default="2048",
    )
    clusters: IntProperty(name="颜色区域数", default=4, min=2, max=8)
    normal_strength: FloatProperty(
        name="细节法线强度",
        description="控制微表面法线的整体强度；1.5 为推荐值",
        default=1.5,
        min=0.0,
        max=4.0,
        soft_min=0.5,
        soft_max=2.5,
    )
    basecolor_normal_strength: FloatProperty(
        name="颜色缝隙强度",
        description="从 BaseColor 局部暗线提取凹槽；0 为关闭，图案或烘焙阴影可能被误判",
        default=0.75,
        min=0.0,
        max=3.0,
        soft_max=1.5,
    )
    basecolor_feature_size: IntProperty(
        name="暗缝宽度",
        description="希望提取的暗缝尺度，以 2048 贴图像素为基准",
        default=12,
        min=2,
        max=64,
    )
    normal_convention: EnumProperty(
        name="法线格式",
        items=[
            ("opengl", "OpenGL / Blender", "绿通道 +Y，适用于 Blender、glTF"),
            ("directx", "DirectX / Unreal", "翻转绿通道，适用于常见 Unreal 工作流"),
        ],
        default="opengl",
    )
    normal_margin: IntProperty(
        name="法线 Padding",
        description="UV 岛向外扩张的烘焙边距",
        default=24,
        min=4,
        max=96,
    )
    device: EnumProperty(
        name="AO 设备",
        items=[("cpu", "CPU", "最稳定"), ("cuda", "NVIDIA CUDA", "更快，失败时自动回退 CPU")],
        default="cpu",
    )
    manual_mask: StringProperty(name="手工 ID Mask", subtype="FILE_PATH")
    assignments: StringProperty(name="材质分配 JSON", subtype="FILE_PATH")
    debug_output: BoolProperty(name="保存调试图", default=False)
    skip_ao: BoolProperty(name="跳过 AO", default=False)
    skip_preview: BoolProperty(name="跳过预览渲染", default=False)
    maps_only: BoolProperty(
        name="仅输出贴图",
        description="不生成 JSON、预览图片和 Blend 文件",
        default=True,
    )
    assetport_package: BoolProperty(
        name="生成 AssetPort / UE5 导入包",
        description="额外导出 FBX、BaseColor、DirectX Normal 与 ORM，并按 AssetPort-CN 规范命名",
        default=False,
    )
    assetport_category: EnumProperty(
        name="UE5 分类",
        items=[
            ("env", "Environment", "/Game/Environment"),
            ("prop", "Props", "/Game/Props"),
            ("wpn", "Weapons", "/Game/Weapons"),
            ("char", "Characters", "/Game/Characters"),
            ("veh", "Vehicles", "/Game/Vehicles"),
            ("fx", "Effects", "/Game/Effects"),
        ],
        default="env",
    )
    assetport_name: StringProperty(
        name="UE5 资产名",
        description="单个模式可自定义；留空使用输入文件名，批量模式始终使用各文件名",
        default="",
    )
    auto_open: BoolProperty(name="完成后打开输出文件夹", default=True)
    show_advanced: BoolProperty(name="高级设置", default=False)
    status: StringProperty(name="状态", default="等待选择资产")
    last_output: StringProperty(default="")


class LMR_OT_Process(Operator):
    bl_idname = "lmr.process"
    bl_label = "生成 PBR"
    bl_description = "在独立的 Blender 后台进程中生成 PBR，不会修改当前场景"
    bl_options = {"REGISTER"}

    def execute(self, context):
        global _job
        if _job is not None and _job["process"].poll() is None:
            self.report({"WARNING"}, "已有任务正在运行")
            return {"CANCELLED"}

        settings = context.scene.lmr_settings
        batch = settings.input_mode == "batch"
        if batch:
            input_dir = Path(bpy.path.abspath(settings.batch_input_dir)).resolve()
            if not input_dir.is_dir():
                self.report({"ERROR"}, "请选择有效的批量输入文件夹")
                return {"CANCELLED"}
            candidates = input_dir.rglob("*") if settings.batch_recursive else input_dir.iterdir()
            assets = sorted(
                (path.resolve() for path in candidates if path.is_file() and path.suffix.lower() in {".glb", ".gltf"}),
                key=lambda path: str(path).casefold(),
            )
            if not assets:
                self.report({"ERROR"}, "输入文件夹中没有 GLB/GLTF")
                return {"CANCELLED"}
        else:
            asset = Path(bpy.path.abspath(settings.asset_path)).resolve()
            if not asset.is_file():
                self.report({"ERROR"}, "请选择有效的 GLB/GLTF 文件")
                return {"CANCELLED"}
            if asset.suffix.lower() not in {".glb", ".gltf"}:
                self.report({"ERROR"}, "当前版本只支持 GLB/GLTF")
                return {"CANCELLED"}
            assets = [asset]

        addon_dir = Path(__file__).resolve().parent
        worker = addon_dir / "pbr_resolver.py"
        materials = addon_dir / "materials.json"
        if not worker.is_file() or not materials.is_file():
            self.report({"ERROR"}, "Add-on 文件不完整，请重新安装 ZIP")
            return {"CANCELLED"}

        if settings.output_dir.strip():
            selected_output_root = Path(bpy.path.abspath(settings.output_dir)).resolve()
        else:
            selected_output_root = input_dir if batch else assets[0].parent
        selected_output_root.mkdir(parents=True, exist_ok=True)
        if batch:
            result_root = _unique_output_directory(selected_output_root, "PBR_Batch")
            result_root.mkdir(parents=True, exist_ok=True)
            output_root = result_root
            assetport_output_root = result_root / "AssetPort_Import"
        else:
            result_root = None
            output_root = selected_output_root
            assetport_output_root = None

        common_args = [
            "--materials",
            str(materials),
            "--resolution",
            settings.resolution,
            "--clusters",
            str(settings.clusters),
            "--device",
            settings.device,
            "--normal-strength",
            str(settings.normal_strength),
            "--basecolor-normal-strength",
            str(settings.basecolor_normal_strength),
            "--basecolor-feature-size",
            str(settings.basecolor_feature_size),
            "--normal-method",
            "blender_bake",
            "--normal-convention",
            settings.normal_convention,
            "--normal-margin",
            str(settings.normal_margin),
        ]
        optional_paths = () if batch else (
            ("--manual-mask", settings.manual_mask),
            ("--assignments", settings.assignments),
        )
        for flag, raw_path in optional_paths:
            if raw_path.strip():
                resolved = Path(bpy.path.abspath(raw_path)).resolve()
                if not resolved.is_file():
                    self.report({"ERROR"}, f"文件不存在：{resolved}")
                    return {"CANCELLED"}
                common_args.extend((flag, str(resolved)))
        if settings.debug_output:
            common_args.append("--debug")
        if settings.skip_ao:
            common_args.append("--skip-ao")
        if settings.skip_preview:
            common_args.append("--skip-preview")
        if settings.maps_only:
            common_args.append("--maps-only")

        _job = {
            "process": None,
            "pending_assets": [str(path) for path in assets],
            "total": len(assets),
            "current_index": 0,
            "batch": batch,
            "maps_only": bool(settings.maps_only),
            "output_root": str(output_root),
            "result_root": str(result_root) if batch else "",
            "worker": str(worker),
            "addon_dir": str(addon_dir),
            "common_args": common_args,
            "auto_open": settings.auto_open,
            "completed_outputs": [],
            "failures": [],
            "cancelled": False,
            "assetport_package": bool(settings.assetport_package),
            "assetport_category": settings.assetport_category,
            "assetport_name": settings.assetport_name.strip(),
            "assetport_output_root": (
                str(assetport_output_root) if assetport_output_root is not None else ""
            ),
        }
        try:
            _launch_next_asset(_job)
        except Exception:
            _job = None
            raise
        if not batch:
            _job["result_root"] = _job["output_dir"]
        settings.status = "正在启动后台 Blender…"
        bpy.app.timers.register(_poll_job, first_interval=0.5)
        message = f"批量任务已开始，共 {len(assets)} 个资产" if batch else "PBR 任务已开始，可继续操作当前 Blender"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class LMR_OT_Cancel(Operator):
    bl_idname = "lmr.cancel"
    bl_label = "停止任务"

    def execute(self, context):
        global _job
        if _job is None or _job["process"].poll() is not None:
            self.report({"INFO"}, "没有正在运行的任务")
            return {"CANCELLED"}
        _job["cancelled"] = True
        _job["pending_assets"].clear()
        _job["process"].terminate()
        context.scene.lmr_settings.status = "正在停止…"
        return {"FINISHED"}


class LMR_OT_OpenOutput(Operator):
    bl_idname = "lmr.open_output"
    bl_label = "打开输出文件夹"

    def execute(self, context):
        path = context.scene.lmr_settings.last_output
        if not path or not Path(path).is_dir():
            self.report({"WARNING"}, "还没有可打开的输出目录")
            return {"CANCELLED"}
        if os.name == "nt":
            os.startfile(path)
        return {"FINISHED"}


class LMR_PT_Main(Panel):
    bl_label = "Local Material Resolver"
    bl_idname = "LMR_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PBR Resolver"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.lmr_settings
        running = _job is not None and _job["process"].poll() is None

        column = layout.column(align=True)
        column.prop(settings, "input_mode", expand=True)
        if settings.input_mode == "batch":
            column.prop(settings, "batch_input_dir")
            column.prop(settings, "batch_recursive")
        else:
            column.prop(settings, "asset_path")
        column.prop(settings, "output_dir")
        row = column.row(align=True)
        row.prop(settings, "resolution")
        row.prop(settings, "device")
        column.prop(settings, "clusters")
        column.prop(settings, "normal_strength")
        column.prop(settings, "basecolor_normal_strength")
        column.prop(settings, "maps_only")

        bridge = layout.box()
        bridge.prop(settings, "assetport_package")
        if settings.assetport_package:
            bridge.prop(settings, "assetport_category")
            if settings.input_mode == "single":
                bridge.prop(settings, "assetport_name")
            bridge.label(text="UE 法线固定为 DirectX", icon="INFO")

        box = layout.box()
        box.prop(settings, "show_advanced", icon="TRIA_DOWN" if settings.show_advanced else "TRIA_RIGHT")
        if settings.show_advanced:
            if settings.input_mode == "single":
                box.prop(settings, "manual_mask")
                box.prop(settings, "assignments")
            box.prop(settings, "basecolor_feature_size")
            box.prop(settings, "normal_convention")
            box.prop(settings, "normal_margin")
            box.prop(settings, "debug_output")
            box.prop(settings, "skip_ao")
            if not settings.maps_only:
                box.prop(settings, "skip_preview")
            box.prop(settings, "auto_open")

        row = layout.row(align=True)
        if running:
            row.operator("lmr.cancel", icon="CANCEL")
        else:
            label = "批量生成 PBR" if settings.input_mode == "batch" else "生成 PBR"
            row.operator("lmr.process", icon="PLAY", text=label)
        row.operator("lmr.open_output", icon="FILE_FOLDER", text="")

        status_box = layout.box()
        status_box.label(text="状态")
        status_box.label(text=settings.status, icon="TIME" if running else "INFO")
        layout.label(text="提示：首次先用 512 测试", icon="LIGHT")


classes = (
    LMR_Settings,
    LMR_OT_Process,
    LMR_OT_Cancel,
    LMR_OT_OpenOutput,
    LMR_PT_Main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lmr_settings = PointerProperty(type=LMR_Settings)


def unregister():
    global _job
    if _job is not None and _job["process"].poll() is None:
        _job["process"].terminate()
    _job = None
    if hasattr(bpy.types.Scene, "lmr_settings"):
        del bpy.types.Scene.lmr_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
