"""Import contracts; runtime behavior lives in ui/models/<id>.js."""

from collections.abc import Callable
from dataclasses import dataclass

DEEPSEEK_EXPRESSIONS = (
    "脸红",
    "流泪",
    "爱心眼",
    "圈圈眼",
    "屑屑眼",
    "＞＜",
    "流冷汗",
    "黑脸",
    "加载",
    "兔牙",
    "单片眼镜",
    "黑框眼镜",
    "钢盆",
    "体型1",
    "体型2",
    "默认体型",
    "前倾",
    "尾巴开关",
    "裙子切换",
    "脱鞋",
    "胸口衣服",
)
DEEPSEEK_WORK = (
    "Animation/竖手指切出来动画.motion3.json",
    "Animation/竖手指切回默认.motion3.json",
    "Animation/默认手切举手.motion3.json",
    "Animation/举手切默认手.motion3.json",
)
DRAGON_EXPRESSIONS = ("抬手", "抬手2", "开扇", "扇面", "变")
DRAGON_MOTIONS = ("idle.motion3.json", "sleep2.motion3.json")


def adapt_dragon(manifest: dict, available: set[str]) -> tuple[dict, tuple[str, ...]]:
    """Normalize the original dragon export and its prepared quality variants."""
    required = {f"{name}.exp3.json" for name in DRAGON_EXPRESSIONS}
    required.update(DRAGON_MOTIONS)
    if not required <= available:
        raise ValueError("模型与龙娘适配器不匹配：缺少所需表情或动作。")
    refs = dict(manifest["FileReferences"])
    if not refs.get("Physics"):
        raise ValueError("缺少模型物理文件。")
    refs["Expressions"] = [
        {"Name": name, "File": f"{name}.exp3.json"} for name in DRAGON_EXPRESSIONS
    ]
    refs["Motions"] = {
        "Idle": [{"File": DRAGON_MOTIONS[0], "FadeInTime": 0.5, "FadeOutTime": 0.5}],
        "Sleep": [{"File": DRAGON_MOTIONS[1], "FadeInTime": 0.7, "FadeOutTime": 0.5}],
    }
    return {**manifest, "FileReferences": refs}, ()


def adapt_deepseek(manifest: dict, available: set[str]) -> tuple[dict, tuple[str, ...]]:
    """Normalize the original export without relying on its motion grouping."""
    refs = dict(manifest["FileReferences"])
    expressions = [
        {"Name": path.removeprefix("Exp3/").removesuffix(".exp3.json"), "File": path}
        for path in sorted(available)
        if path.startswith("Exp3/") and path.endswith(".exp3.json")
    ]
    names = {item["Name"] for item in expressions}
    if not set(DEEPSEEK_EXPRESSIONS) <= names:
        raise ValueError("模型与大肥鱼适配器不匹配：缺少所需表情。")
    refs["Expressions"] = expressions
    refs["Motions"] = {"Idle": [{"File": "Animation/IDLE.motion3.json"}]}
    if not refs.get("Physics"):
        raise ValueError("缺少模型物理文件。")
    return {**manifest, "FileReferences": refs}, DEEPSEEK_WORK


@dataclass(frozen=True)
class ImportAdapter:
    normalize: Callable[[dict, set[str]], tuple[dict, tuple[str, ...]]]
    # Only known original exports may omit an explicit adapter declaration.
    legacy_files: frozenset[str] = frozenset()


IMPORT_ADAPTERS = {
    "dragon": ImportAdapter(
        adapt_dragon,
        frozenset(
            [f"{name}.exp3.json" for name in DRAGON_EXPRESSIONS]
            + list(DRAGON_MOTIONS)
            + ["kohaku.moc3", "kohaku.physics3.json"]
        ),
    ),
    "deepseek": ImportAdapter(
        adapt_deepseek,
        frozenset(
            [f"Exp3/{name}.exp3.json" for name in DEEPSEEK_EXPRESSIONS]
            + list(DEEPSEEK_WORK)
            + ["Animation/IDLE.motion3.json"]
        ),
    ),
}


def resolve_adapter(manifest: dict, available: set[str], expected: str | None) -> str:
    """Resolve a data-only binding, never executable code from the archive."""
    declared = []
    if "KohakuTerrarium" in manifest:
        binding = manifest["KohakuTerrarium"]
        if not isinstance(binding, dict) or not isinstance(binding.get("adapter"), str):
            raise ValueError("模型的 KohakuTerrarium.adapter 声明不合法。")
        declared.append(binding["adapter"])
    legacy = manifest.get("Demo")
    if isinstance(legacy, dict) and "profile" in legacy:
        if not isinstance(legacy["profile"], str):
            raise ValueError("模型的 Demo.profile 声明不合法。")
        declared.append(legacy["profile"])
    if len(set(declared)) > 1:
        raise ValueError("模型文件中的适配器声明不一致。")
    if declared:
        model = declared[0]
        if model not in IMPORT_ADAPTERS:
            raise ValueError("此模型尚未适配，请先添加对应适配器。")
    else:
        matches = [
            name
            for name, adapter in IMPORT_ADAPTERS.items()
            if adapter.legacy_files and adapter.legacy_files <= available
        ]
        if len(matches) != 1:
            raise ValueError(
                "无法识别模型，请确认资源完整且已在模型文件中声明对应适配器。"
            )
        model = matches[0]
    if expected is not None and expected != model:
        raise ValueError("资源包与请求导入的模型不匹配。")
    return model
