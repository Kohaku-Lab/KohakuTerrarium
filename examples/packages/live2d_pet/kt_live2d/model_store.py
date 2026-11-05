"""Bounded ZIP import into private, immutable model versions."""

import copy
import io
import json
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image

from .model_adapters import IMPORT_ADAPTERS, resolve_adapter

MAX_UPLOAD = 128 * 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
VERSION = re.compile(r"[a-f0-9]{32}\Z")


def safe_path(name: str) -> str:
    """Reject traversal, URL-like paths and platform-dependent separators."""
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or any(c in name for c in "\x00?#%")
        or PurePosixPath(name).is_absolute()
        or any(part in (".", "..", "") for part in name.split("/"))
    ):
        raise ValueError("资源路径不合法。")
    return name


def read_json(data: bytes) -> dict:
    if len(data) > MAX_JSON:
        raise ValueError("模型描述文件过大。")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("模型描述不是有效 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError("模型描述应为 JSON 对象。")
    return value


def current_model(root: Path, model: str) -> dict | None:
    try:
        version = (root / model / "current").read_text().strip()
        if not VERSION.fullmatch(version):
            return None
        info = read_json((root / model / version / "inventory.json").read_bytes())
        return {"model": model, "version": version, "bytes": info["bytes"]}
    except (OSError, ValueError, KeyError):
        return None


def model_file(root: Path, model: str, version: str, name: str) -> Path:
    if model not in IMPORT_ADAPTERS or not VERSION.fullmatch(version):
        raise ValueError("模型资源不存在。")
    folder = root / model / version
    safe_path(name)
    inventory = read_json((folder / "inventory.json").read_bytes())
    target = (folder / name).resolve()
    if name not in inventory["files"] or not target.is_relative_to(folder.resolve()):
        raise ValueError("模型资源不存在。")
    return target


def _archive_files(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    if len(entries) > 512 or sum(x.file_size for x in entries) > MAX_UNPACKED:
        raise ValueError("ZIP 超出限制：最多 512 项，解压后最多 512 MiB。")
    files = {}
    for info in entries:
        name = info.filename.rstrip("/") if info.is_dir() else info.filename
        safe_path(name)
        if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
            raise ValueError("ZIP 不能包含链接或加密文件。")
        if info.is_dir() or name.startswith("__MACOSX/"):
            continue
        if name in files:
            raise ValueError("ZIP 存在重复路径。")
        files[name] = info
    return files


def _prepare(archive: zipfile.ZipFile, destination: Path, model: str | None) -> dict:
    files = _archive_files(archive)
    manifests = [path for path in files if path.endswith(".model3.json")]
    # Prepared exports may contain both qualities. Always derive from the best one.
    originals = [
        p
        for p in manifests
        if PurePosixPath(p).name not in ("high.model3.json", "standard.model3.json")
    ]
    candidates = originals or [
        p
        for p in manifests
        if p.endswith("/high.model3.json") or p == "high.model3.json"
    ]
    if len(candidates) != 1:
        raise ValueError("请选择只包含一个模型的运行资源 ZIP（含 .model3.json）。")
    path = candidates[0]
    base = str(PurePosixPath(path).parent)
    prefix = "" if base == "." else base + "/"
    available = {p[len(prefix) :] for p in files if p.startswith(prefix)}

    def read(name: str) -> bytes:
        safe_path(name)
        try:
            return archive.read(files[prefix + name])
        except KeyError as exc:
            raise ValueError(f"缺少模型资源：{name}") from exc

    manifest = read_json(read(path[len(prefix) :]))
    refs = manifest.get("FileReferences")
    if manifest.get("Version") != 3 or not isinstance(refs, dict):
        raise ValueError("需要 Cubism 3 格式的模型描述。")
    model = resolve_adapter(manifest, available, model)
    manifest, extra = IMPORT_ADAPTERS[model].normalize(manifest, available)
    manifest["KohakuTerrarium"] = {"adapter": model}
    refs = manifest["FileReferences"]
    textures = refs.get("Textures")
    if not isinstance(textures, list) or not 1 <= len(textures) <= 8:
        raise ValueError("需要 1–8 张模型纹理。")
    moc = refs.get("Moc")
    data = read(moc)
    if not moc.endswith(".moc3") or not data.startswith(b"MOC3") or len(data) < 64:
        raise ValueError("不是有效的 MOC3 模型。")
    resources = {moc, *extra}
    resources.update(
        refs[key]
        for key in ("Physics", "DisplayInfo", "Pose", "UserData")
        if refs.get(key)
    )
    resources.update(item["File"] for item in refs["Expressions"])
    resources.update(
        item["File"] for group in refs["Motions"].values() for item in group
    )
    for name in resources:
        data = read(name)
        if name != moc:
            if not name.endswith(".json"):
                raise ValueError("模型引用了不支持的资源类型。")
            definition = read_json(data)
            if name.endswith(".motion3.json") and not isinstance(
                definition.get("Curves"), list
            ):
                raise ValueError(f"动作文件不完整：{name}")
            if name.endswith(".exp3.json") and not isinstance(
                definition.get("Parameters"), list
            ):
                raise ValueError(f"表情文件不完整：{name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    # Preserve supplied text notices, but never publish scripts or authoring files.
    for name in available:
        if (
            name.lower().endswith(".txt")
            and files[prefix + name].file_size <= 128 * 1024
        ):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(name))
    variants = {quality: copy.deepcopy(manifest) for quality in ("high", "standard")}
    dimensions = {quality: [] for quality in variants}
    largest = 0
    for name in textures:
        with Image.open(io.BytesIO(read(name))) as image:
            largest = max(largest, image.width, image.height)
    high_divisor = max(1, largest / 4096)
    for index, name in enumerate(textures):
        with Image.open(io.BytesIO(read(name))) as original:
            if (
                original.format not in ("PNG", "WEBP")
                or getattr(original, "n_frames", 1) != 1
            ):
                raise ValueError("纹理必须是单帧 PNG 或 WebP。")
            if original.width * original.height > 8192**2:
                raise ValueError("单张纹理不能超过 8192² 像素。")
            rgba = original.convert("RGBA")
            for quality in ("high", "standard"):
                divisor = high_divisor * (1 if quality == "high" else 2)
                size = (
                    max(1, round(rgba.width / divisor)),
                    max(1, round(rgba.height / divisor)),
                )
                scaled = (
                    rgba
                    if size == rgba.size
                    else rgba.resize(size, Image.Resampling.LANCZOS)
                )
                name = f"{quality}/texture_{index}.webp"
                target = destination / name
                target.parent.mkdir(exist_ok=True)
                scaled.save(
                    target,
                    format="WEBP",
                    lossless=True,
                    exact=True,
                    method=2,
                    icc_profile=original.info.get("icc_profile", b""),
                )
                dimensions[quality].append(
                    {"file": name, "width": scaled.width, "height": scaled.height}
                )
    for quality, variant in variants.items():
        variant["FileReferences"]["Textures"] = [
            item["file"] for item in dimensions[quality]
        ]
        variant["Demo"] = {
            "profile": model,
            "quality": quality,
            "textures": dimensions[quality],
            "textureBytes": sum(
                item["width"] * item["height"] * 4 for item in dimensions[quality]
            ),
        }
        (destination / f"{quality}.model3.json").write_text(
            json.dumps(variant, ensure_ascii=False), encoding="utf-8"
        )
    paths = [p for p in destination.rglob("*") if p.is_file()]
    inventory = {
        "model": model,
        "files": [p.relative_to(destination).as_posix() for p in paths],
        "bytes": sum(p.stat().st_size for p in paths),
    }
    (destination / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    return inventory


def import_model(root: Path, model: str | None, source: Path) -> dict:
    if model is not None and model not in IMPORT_ADAPTERS:
        raise ValueError("此模型没有资源导入适配器。")
    if source.stat().st_size > MAX_UPLOAD:
        raise ValueError("ZIP 不能超过 128 MiB。")
    root.mkdir(parents=True, exist_ok=True)
    version = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temp:
        staging = Path(temp) / "model"
        staging.mkdir()
        try:
            with zipfile.ZipFile(source) as archive:
                inventory = _prepare(archive, staging, model)
        except (
            zipfile.BadZipFile,
            RuntimeError,
            OSError,
            TypeError,
            KeyError,
            Image.DecompressionBombError,
        ) as exc:
            raise ValueError(
                "无法导入：ZIP 或模型资源损坏，请选择完整的运行资源包。"
            ) from exc
        model = inventory["model"]
        folder = root / model
        folder.mkdir(parents=True, exist_ok=True)
        staging.rename(folder / version)
        pointer = Path(temp) / "current"
        pointer.write_text(version)
        try:
            pointer.replace(folder / "current")
        except OSError:
            shutil.rmtree(folder / version)
            raise
    return {"model": model, "version": version, "bytes": inventory["bytes"]}
