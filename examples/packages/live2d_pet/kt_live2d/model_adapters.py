"""Import contracts; runtime behavior lives in ui/models/<id>.js."""

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


IMPORT_ADAPTERS = {"deepseek": adapt_deepseek}
