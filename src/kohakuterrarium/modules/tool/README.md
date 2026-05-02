# modules/tool/

Tool protocol and base classes. Tools are executable functions that the
controller invokes via parsed tool call blocks. Each tool declares its name,
description, execution mode, and whether it needs context injection. Tools
return `ToolResult` with output text, optional exit code, error, metadata,
and multimodal content (images).

## Files

| File          | Description                                                                                                  |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| `__init__.py` | Re-exports protocol, base class, and all data types                                                          |
| `base.py`     | `Tool` protocol, `BaseTool` ABC, `ToolConfig`, `ToolContext`, `ToolResult`, `ToolInfo`, `ExecutionMode` enum |

## Dependencies

- `kohakuterrarium.core.session` (Session, via ToolContext)
- `kohakuterrarium.llm.message` (ContentPart, ImagePart, TextPart, for multimodal results)
