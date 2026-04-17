"""Unified merge logic for creature config inheritance.

The rules are deliberately small and apply to every field type:

- **Scalars** — child overrides.
- **Dicts** (``controller``, ``input``, ``output``, ``memory``,
  ``compact``, …) — shallow merge; child keys override at the top level.
- **Identity-keyed lists** (see :data:`_LIST_IDENTITY`) — union by the
  identity field (``name``). On identity collision the **child wins**
  and replaces the base entry in place, preserving base order. Items
  without the identity value concatenate.
- **Other lists** — child replaces base.

Two directives opt out of defaults:

- ``no_inherit: [field, …]`` — drops the inherited value for each
  listed field entirely. Applies uniformly to scalars, dicts, identity
  lists, and the accumulated prompt chain.
- ``prompt_mode: concat | replace`` — ``concat`` (default) keeps the
  inherited prompt file chain and inline prompt. ``replace`` is sugar
  for ``no_inherit: [system_prompt, system_prompt_file]`` plus a wipe
  of the ``_prompt_chain`` accumulator.
"""

from typing import Any

# Identity-keyed lists: items with the identity value present are merged
# child-wins and replace the base entry in place. Items without an
# identity value concatenate.
_LIST_IDENTITY: dict[str, str] = {
    "tools": "name",
    "subagents": "name",
    "plugins": "name",
    "mcp_servers": "name",
    "triggers": "name",  # optional; triggers without name just append
}


def merge_configs(base_data: dict[str, Any], child_data: dict[str, Any]) -> dict:
    """Merge child config over base config following the unified rule set.

    See module docstring for the full rule set. This function is pure and
    returns a new dict; it does not mutate its inputs.
    """
    no_inherit: set[str] = set(child_data.get("no_inherit", []))
    prompt_mode = child_data.get("prompt_mode", "concat")

    if prompt_mode == "replace":
        no_inherit.update({"system_prompt", "system_prompt_file"})

    drop_prompt_chain = "system_prompt_file" in no_inherit or prompt_mode == "replace"
    drop_inline_prompt = "system_prompt" in no_inherit or prompt_mode == "replace"

    # Seed the result from base, dropping anything the child has opted out of.
    result: dict[str, Any] = {}
    for k, v in base_data.items():
        if k in no_inherit:
            continue
        if k == "_prompt_chain" and drop_prompt_chain:
            continue
        if k == "_inline_system_prompt" and drop_inline_prompt:
            continue
        result[k] = v

    # Track inline system_prompt from child (for _load_prompt_chain to append).
    # Note: `drop_inline_prompt` above only strips inherited inline from the
    # base. The child's own inline always goes through if it is set.
    if "system_prompt" in child_data and child_data["system_prompt"] is not None:
        result["_inline_system_prompt"] = child_data["system_prompt"]

    for key, value in child_data.items():
        if key in ("base_config", "no_inherit", "prompt_mode"):
            continue  # Metadata, don't propagate
        if value is None:
            continue  # Only override if child explicitly sets a value

        identity = _LIST_IDENTITY.get(key)
        if (
            identity
            and isinstance(value, list)
            and key in result
            and isinstance(result[key], list)
        ):
            result[key] = _merge_identity_list(result[key], value, identity)
        elif (
            isinstance(value, dict) and key in result and isinstance(result[key], dict)
        ):
            # Shallow merge for dicts.
            merged_dict = dict(result[key])
            merged_dict.update(value)
            result[key] = merged_dict
        else:
            # Scalars and lists without identity: child replaces base.
            result[key] = value
    return result


def _merge_identity_list(base_list: list, child_list: list, identity: str) -> list:
    """Union two lists by an identity field, with child-wins on collision.

    Preserves base's positional order: base entries appear first, a child
    entry with a matching identity replaces its base entry in place, and
    new child entries (or entries without the identity value) are
    appended.
    """
    base_index: dict[Any, int] = {}
    for i, item in enumerate(base_list):
        if isinstance(item, dict):
            ident = item.get(identity)
            if ident is not None:
                base_index[ident] = i

    merged_list = list(base_list)
    for item in child_list:
        ident = item.get(identity) if isinstance(item, dict) else None
        if ident is not None and ident in base_index:
            merged_list[base_index[ident]] = item
        else:
            merged_list.append(item)
    return merged_list
