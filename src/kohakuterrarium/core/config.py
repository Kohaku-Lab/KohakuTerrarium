"""
Configuration loading and validation for KohakuTerrarium agents.

Supports YAML, JSON, and TOML formats with environment variable interpolation.
"""

import json
from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.core.config_merge import merge_configs as _merge_configs
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    OutputConfigItem,
    SubAgentConfigItem,
    ToolConfigItem,
    TriggerConfig,
    _interpolate_env_vars,
)
from kohakuterrarium.core.output_wiring import parse_wiring_list
from kohakuterrarium.packages.resolve import resolve_package_path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from kohakuterrarium.prompt.template import render_template_safe
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path) -> dict[str, Any]:
    """Load JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_toml(path: Path) -> dict[str, Any]:
    """Load TOML file."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def _find_config_file(agent_path: Path) -> Path | None:
    """Find config file in agent folder."""
    for name in ["config.yaml", "config.yml", "config.json", "config.toml"]:
        path = agent_path / name
        if path.exists():
            return path
    return None


def _load_config_file(path: Path) -> dict[str, Any]:
    """Load config file based on extension."""
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    elif suffix == ".json":
        return _load_json(path)
    elif suffix == ".toml":
        return _load_toml(path)
    else:
        raise ValueError(f"Unsupported config format: {suffix}")


def _resolve_base_config_path(base_config: str, child_dir: Path) -> Path | None:
    """
    Resolve base_config path to an actual directory.

    Resolution order:
    1. If starts with '@', resolve as package reference (@package/path)
    2. If starts with 'creatures/', resolve relative to project root
       (walk up from child_dir until we find a directory containing 'creatures/')
    3. Otherwise resolve relative to child config's parent directory
    """
    # Package reference: @package-name/creatures/swe
    # Strip quotes first (YAML may quote the @ as "@...")
    clean = base_config.strip('"').strip("'")
    if clean.startswith("@"):
        try:
            return resolve_package_path(clean)
        except (FileNotFoundError, ValueError) as e:
            logger.warning(
                "Package reference failed", base_config=base_config, error=str(e)
            )
            return None

    if base_config.startswith("creatures/"):
        # Walk up from child_dir to find project root (containing creatures/)
        search = child_dir
        for _ in range(10):  # safety limit
            candidate = search / base_config
            if candidate.is_dir():
                return candidate
            parent = search.parent
            if parent == search:
                break
            search = parent
        logger.warning(
            "Could not resolve creatures/ path from project root",
            base_config=base_config,
            child_dir=str(child_dir),
        )
        return None

    # Relative to child config's parent directory
    resolved = (child_dir / base_config).resolve()
    if resolved.is_dir():
        return resolved
    logger.warning(
        "Base config directory not found",
        base_config=base_config,
        resolved=str(resolved),
    )
    return None


# The unified merge implementation lives in `core/config_merge.py`.
# It is re-exported above as `_merge_configs` so existing imports keep working.


def _load_base_config_data(base_path: Path) -> dict[str, Any] | None:
    """Load raw config data from a base config directory."""
    config_file = _find_config_file(base_path)
    if config_file is None:
        logger.warning("No config file in base directory", path=str(base_path))
        return None

    raw = _load_config_file(config_file)
    data = _interpolate_env_vars(raw)

    # Recursively resolve base_config if the base also has one
    if "base_config" in data and data["base_config"]:
        grandparent_path = _resolve_base_config_path(data["base_config"], base_path)
        if grandparent_path:
            grandparent_data = _load_base_config_data(grandparent_path)
            if grandparent_data:
                data = _merge_configs(grandparent_data, data)

    # Track prompt files with their resolved paths for append chain
    prompt_file = data.get("system_prompt_file")
    if prompt_file:
        prompt_path = base_path / prompt_file
        if prompt_path.exists():
            existing_chain = data.get("_prompt_chain", [])
            data["_prompt_chain"] = existing_chain + [str(prompt_path)]

    return data


def _parse_input_config(data: dict[str, Any] | None) -> InputConfig:
    """Parse input configuration."""
    if data is None:
        return InputConfig()
    reserved = {"type", "module", "class", "prompt"}
    return InputConfig(
        type=data.get("type", "cli"),
        module=data.get("module"),
        class_name=data.get("class"),
        prompt=data.get("prompt", "> "),
        options={k: v for k, v in data.items() if k not in reserved},
    )


def _parse_trigger_config(data: dict[str, Any]) -> TriggerConfig:
    """Parse trigger configuration."""
    reserved = {"type", "module", "class", "prompt", "name"}
    return TriggerConfig(
        type=data.get("type", ""),
        module=data.get("module"),
        class_name=data.get("class"),
        prompt=data.get("prompt"),
        name=data.get("name"),
        options={k: v for k, v in data.items() if k not in reserved},
    )


def _parse_tool_config(data: dict[str, Any]) -> ToolConfigItem:
    """Parse tool configuration."""
    reserved = {"name", "type", "module", "class", "doc"}
    return ToolConfigItem(
        name=data.get("name", ""),
        type=data.get("type", "builtin"),
        module=data.get("module"),
        class_name=data.get("class"),
        doc=data.get("doc"),
        options={k: v for k, v in data.items() if k not in reserved},
    )


def _parse_output_config_item(data: dict[str, Any]) -> OutputConfigItem:
    """Parse a single output configuration item."""
    reserved = {"type", "module", "class"}
    return OutputConfigItem(
        type=data.get("type", "stdout"),
        module=data.get("module"),
        class_name=data.get("class"),
        options={k: v for k, v in data.items() if k not in reserved},
    )


def _parse_output_config(data: dict[str, Any] | None) -> OutputConfig:
    """Parse output configuration."""
    if data is None:
        return OutputConfig()

    # Parse named outputs if present
    named_outputs: dict[str, OutputConfigItem] = {}
    if "named_outputs" in data:
        for name, item_data in data["named_outputs"].items():
            named_outputs[name] = _parse_output_config_item(item_data)

    reserved = {"type", "module", "class", "controller_direct", "named_outputs"}
    return OutputConfig(
        type=data.get("type", "stdout"),
        module=data.get("module"),
        class_name=data.get("class"),
        controller_direct=data.get("controller_direct", True),
        options={k: v for k, v in data.items() if k not in reserved},
        named_outputs=named_outputs,
    )


def _parse_subagent_config(data: dict[str, Any] | str) -> SubAgentConfigItem:
    """Parse sub-agent configuration."""
    if isinstance(data, str):
        return SubAgentConfigItem(name=data)
    # Fields that are handled explicitly
    reserved = {
        "name",
        "type",
        "module",
        "config",
        "description",
        "tools",
        "can_modify",
        "interactive",
    }
    # All other fields (prompt_file, output_to, context_mode, max_turns, etc.)
    # go into options for inline custom sub-agent configs
    return SubAgentConfigItem(
        name=data.get("name", ""),
        type=data.get("type", "builtin"),
        module=data.get("module"),
        config_name=data.get("config"),
        description=data.get("description"),
        tools=data.get("tools", []),
        can_modify=data.get("can_modify", False),
        interactive=data.get("interactive", False),
        options={k: v for k, v in data.items() if k not in reserved},
    )


def load_agent_config(agent_path: str | Path) -> AgentConfig:
    """
    Load agent configuration from a folder *or* a direct config file.

    Args:
        agent_path: Path to the agent folder, or directly to its
            ``config.{yaml,yml,json,toml}`` file. When a file path is
            given, its parent directory is used as the agent folder.

    Returns:
        Loaded AgentConfig

    Raises:
        FileNotFoundError: If config file not found
        ValueError: If config is invalid
    """
    agent_path = Path(agent_path)

    if not agent_path.exists():
        raise FileNotFoundError(f"Agent path not found: {agent_path}")

    # Accept either a folder or a direct config file. When the user
    # points at the file itself, treat its parent as the agent folder
    # so relative references inside the config still resolve correctly.
    if agent_path.is_file():
        config_file = agent_path
        agent_path = agent_path.parent
    else:
        config_file = _find_config_file(agent_path)
        if config_file is None:
            raise FileNotFoundError(f"No config file found in: {agent_path}")

    logger.debug("Loading config", path=str(config_file))
    raw_config = _load_config_file(config_file)

    # Interpolate environment variables
    config_data = _interpolate_env_vars(raw_config)

    return build_agent_config(config_data, agent_path)


def _resolve_inheritance(
    config_data: dict[str, Any], agent_path: Path
) -> dict[str, Any]:
    """Resolve base_config inheritance and merge parent config data.

    If config_data has a base_config reference, loads the base config
    recursively and merges it with the child config.

    Returns:
        Merged config_data (with _base_path set if inheritance was resolved).
    """
    base_config_ref = config_data.get("base_config")
    if not base_config_ref:
        return config_data

    base_path = _resolve_base_config_path(base_config_ref, agent_path)
    if not base_path:
        logger.warning(
            "Base config not found, continuing with child-only config",
            base_config=base_config_ref,
        )
        return config_data

    base_data = _load_base_config_data(base_path)
    if not base_data:
        return config_data

    logger.debug(
        "Merging base config",
        base=str(base_path),
        child=str(agent_path),
    )
    merged = _merge_configs(base_data, config_data)
    merged["_base_path"] = base_path
    return merged


def _construct_agent_config(
    config_data: dict[str, Any], agent_path: Path
) -> AgentConfig:
    """Build the AgentConfig dataclass from a (possibly merged) config dict."""
    controller_data = config_data.get("controller", {})

    return AgentConfig(
        name=config_data.get("name", agent_path.name),
        version=config_data.get("version", "1.0"),
        llm_profile=controller_data.get("llm", config_data.get("llm", "")),
        model=controller_data.get("model", config_data.get("model", "")),
        provider=controller_data.get("provider", config_data.get("provider", "")),
        variation_selections=dict(
            controller_data.get(
                "variation_selections", config_data.get("variation_selections", {})
            )
            or {}
        ),
        variation=controller_data.get("variation", config_data.get("variation", "")),
        auth_mode=controller_data.get("auth_mode", config_data.get("auth_mode", "")),
        api_key_env=controller_data.get(
            "api_key_env", config_data.get("api_key_env", "")
        ),
        base_url=controller_data.get("base_url", config_data.get("base_url", "")),
        temperature=controller_data.get(
            "temperature", config_data.get("temperature", 0.7)
        ),
        max_tokens=controller_data.get(
            "max_tokens", config_data.get("max_tokens", None)
        ),
        reasoning_effort=controller_data.get(
            "reasoning_effort", config_data.get("reasoning_effort", "medium")
        ),
        service_tier=controller_data.get(
            "service_tier", config_data.get("service_tier", None)
        ),
        extra_body=controller_data.get("extra_body", config_data.get("extra_body", {})),
        retry_policy=controller_data.get(
            "retry_policy", config_data.get("retry_policy")
        ),
        system_prompt=config_data.get("system_prompt", "You are a helpful assistant."),
        system_prompt_file=config_data.get("system_prompt_file"),
        prompt_context_files=config_data.get("prompt_context_files", {}),
        skill_mode=controller_data.get(
            "skill_mode", config_data.get("skill_mode", "dynamic")
        ),
        include_tools_in_prompt=controller_data.get(
            "include_tools_in_prompt", config_data.get("include_tools_in_prompt", True)
        ),
        include_hints_in_prompt=controller_data.get(
            "include_hints_in_prompt", config_data.get("include_hints_in_prompt", True)
        ),
        max_messages=controller_data.get("max_messages", 0),
        ephemeral=controller_data.get("ephemeral", False),
        tool_format=controller_data.get("tool_format", "bracket"),
        input=_parse_input_config(config_data.get("input")),
        triggers=[_parse_trigger_config(t) for t in config_data.get("triggers", [])],
        tools=[_parse_tool_config(t) for t in config_data.get("tools", [])],
        subagents=[_parse_subagent_config(s) for s in config_data.get("subagents", [])],
        output=_parse_output_config(config_data.get("output")),
        compact=config_data.get("compact"),
        startup_trigger=config_data.get("startup_trigger"),
        termination=config_data.get("termination"),
        max_subagent_depth=config_data.get("max_subagent_depth", 3),
        max_iterations=config_data.get("max_iterations"),
        default_plugins=list(config_data.get("default_plugins") or []),
        agent_path=agent_path,
        session_key=config_data.get("session_key"),
        mcp_servers=list(config_data.get("mcp_servers") or []),
        plugins=list(config_data.get("plugins") or []),
        memory=dict(config_data.get("memory") or {}),
        output_wiring=parse_wiring_list(config_data.get("output_wiring")),
        skills=list(config_data.get("skills") or []),
        skill_index_budget_bytes=int(config_data.get("skill_index_budget_bytes", 4096)),
        framework_hint_overrides=dict(
            config_data.get("framework_hint_overrides")
            or config_data.get("framework_hints")
            or {}
        ),
    )


def _load_prompt_chain(config: AgentConfig, config_data: dict[str, Any]) -> None:
    """Load system prompt from the file chain (base prompts + child prompt).

    Mutates config.system_prompt in place.  Sources are combined in order:

    1. File-based prompts from the inheritance chain (``_prompt_chain``)
    2. The child's own ``system_prompt_file`` (if not already in chain)
    3. An inline ``system_prompt`` set by the child config
       (tracked as ``_inline_system_prompt`` during merge)

    All present sources are joined with double newlines.
    """
    base_path = config_data.get("_base_path")
    prompt_chain: list[str] = config_data.get("_prompt_chain", [])
    inline_prompt: str | None = config_data.get("_inline_system_prompt")
    prompt_parts: list[str] = []

    # Load all base prompt files from the inheritance chain
    for chain_path in prompt_chain:
        chain_file = Path(chain_path)
        if chain_file.exists():
            with open(chain_file, encoding="utf-8") as f:
                prompt_parts.append(f.read())
            logger.debug("Loaded chain prompt", path=str(chain_file))

    # Load child's own system prompt (from agent_path or base_path fallback)
    if config.system_prompt_file and config.agent_path:
        prompt_path = config.agent_path / config.system_prompt_file
        if not prompt_path.exists() and base_path:
            prompt_path = base_path / config.system_prompt_file
        if prompt_path.exists():
            # Only add if not already in chain (avoid duplicates)
            resolved = str(prompt_path.resolve())
            chain_resolved = [str(Path(p).resolve()) for p in prompt_chain]
            if resolved not in chain_resolved:
                with open(prompt_path, encoding="utf-8") as f:
                    prompt_parts.append(f.read())
                logger.debug("Loaded child prompt", path=str(prompt_path))

    # Append inline system_prompt from child (e.g. terrarium creature override)
    if inline_prompt:
        prompt_parts.append(inline_prompt)
        logger.debug("Appended inline system_prompt to chain")

    if prompt_parts:
        config.system_prompt = "\n\n".join(prompt_parts)
        logger.debug(
            "Assembled system prompt from chain",
            parts=len(prompt_parts),
        )


def _render_prompt_context(config: AgentConfig) -> None:
    """Load prompt context files and render Jinja template variables.

    Mutates config.system_prompt in place if context files are found.
    """
    if not config.prompt_context_files or not config.agent_path:
        return

    context_vars: dict[str, str] = {}
    for var_name, file_path in config.prompt_context_files.items():
        full_path = config.agent_path / file_path
        if full_path.exists():
            with open(full_path, encoding="utf-8") as f:
                context_vars[var_name] = f.read()
            logger.debug(
                "Loaded prompt context file",
                variable=var_name,
                path=str(full_path),
            )
        else:
            logger.warning(
                "Prompt context file not found",
                variable=var_name,
                path=str(full_path),
            )

    if context_vars:
        config.system_prompt = render_template_safe(
            config.system_prompt, **context_vars
        )
        logger.debug(
            "Rendered system prompt with context",
            variables=list(context_vars.keys()),
        )


def build_agent_config(
    config_data: dict[str, Any],
    agent_path: Path,
) -> AgentConfig:
    """
    Build AgentConfig from a raw config dict.

    Handles base_config inheritance, system prompt loading, and
    template rendering. Used by load_agent_config (from file) and
    by terrarium runtime (inline root agent config from dict).

    Args:
        config_data: Raw config dict (env vars interpolated automatically)
        agent_path: Path context for resolving relative paths

    Returns:
        Loaded AgentConfig
    """
    config_data = _interpolate_env_vars(config_data)
    config_data = _resolve_inheritance(config_data, agent_path)
    config = _construct_agent_config(config_data, agent_path)
    _load_prompt_chain(config, config_data)
    _render_prompt_context(config)

    logger.info("Agent config loaded", agent_name=config.name, model=config.model)
    return config
