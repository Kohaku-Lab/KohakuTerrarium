# prompt/

Prompt loading, templating, and system prompt aggregation. Builds the final
system prompt from components: a base prompt file (agent personality), an
auto-generated tool list, framework hints (call syntax, commands), environment
info, and project instructions. Supports compact skill indexes with
on-demand lookup via `info` / `skill` tools. Prompt
composition is plugin-based, with each plugin contributing a prioritized
section.

## Files

| File              | Description                                                                                                                                                                                    |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`     | Re-exports loader, template, and aggregator functions                                                                                                                                          |
| `loader.py`       | `load_prompt`, `load_prompt_with_fallback`, `load_prompts_folder`: read markdown prompt files                                                                                                  |
| `template.py`     | `PromptTemplate`, `render_template`, `render_template_safe`: Jinja2-based variable substitution                                                                                                |
| `aggregator.py`   | `aggregate_system_prompt`, `build_context_message`: compose the gated framework sections                                                                                                        |
| `framework_hints.py` | Six canonical, overrideable prose blocks (execution model, call syntax, output model, untrusted content, group model, group growth)                                                          |
| `tool_contributions.py` | `build_tool_guidance_section`: deterministic `## Tool guidance` from `BaseTool.prompt_contribution()`                                                                                     |
| `skill_loader.py` | Markdown skill/documentation loader with YAML frontmatter support                                                                                                                              |

## Dependencies

- `kohakuterrarium.builtin_skills` (tool and sub-agent doc retrieval)
- `kohakuterrarium.core.registry` (Registry, for tool list generation)
- `kohakuterrarium.parsing.format` (ToolCallFormat, format examples)
- `kohakuterrarium.utils.logging`
- Third-party: `jinja2`, `yaml`
