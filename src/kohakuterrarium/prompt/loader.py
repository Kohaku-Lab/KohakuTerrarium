"""Load UTF-8 prompt text from files and prompt directories."""

from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def load_prompt(path: str | Path) -> str:
    """Read one UTF-8 prompt file, raising when it does not exist."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    with open(path, encoding="utf-8") as f:
        content = f.read()

    logger.debug("Loaded prompt", path=str(path), length=len(content))
    return content


def load_prompts_folder(folder: str | Path) -> dict[str, str]:
    """Load top-level Markdown and text files keyed by filename stem."""
    folder = Path(folder)
    if not folder.exists():
        logger.warning("Prompts folder not found", path=str(folder))
        return {}

    prompts = {}
    for path in folder.iterdir():
        if path.suffix.lower() in (".md", ".txt"):
            name = path.stem
            prompts[name] = load_prompt(path)

    logger.debug("Loaded prompts folder", path=str(folder), count=len(prompts))
    return prompts


def load_prompt_with_fallback(
    primary: str | Path | None,
    fallback: str,
) -> str:
    """Load a prompt file or return fallback when no readable path exists."""
    if primary is None:
        return fallback

    try:
        return load_prompt(primary)
    except FileNotFoundError:
        logger.debug("Prompt file not found, using fallback", path=str(primary))
        return fallback
