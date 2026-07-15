"""Configurable tool call format for the stream parser."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCallFormat:
    """Define delimiters, slash semantics, and argument syntax for tool calls.

    ``slash_means_open`` distinguishes bracket tags such as ``[/name]`` from
    XML tags such as ``</name>``.
    """

    start_char: str = "["
    end_char: str = "]"
    slash_means_open: bool = True
    arg_style: str = "line"  # "line" uses body lines; "inline" uses tag attributes.
    arg_prefix: str = "@@"
    arg_kv_sep: str = "="


BRACKET_FORMAT = ToolCallFormat()
XML_FORMAT = ToolCallFormat(
    start_char="<",
    end_char=">",
    slash_means_open=False,
    arg_style="inline",
    arg_prefix="",
)


def format_tool_call_example(
    fmt: ToolCallFormat,
    name: str,
    args: dict[str, str] | None = None,
    body: str = "",
) -> str:
    """Render a format-correct tool-call example for prompt generation."""
    s, e = fmt.start_char, fmt.end_char

    if fmt.slash_means_open:
        open_tag = f"{s}/{name}{e}"
        close_tag = f"{s}{name}/{e}"
    else:
        open_tag = f"{s}{name}{e}"
        close_tag = f"{s}/{name}{e}"

    if args and fmt.arg_style == "inline":
        attr_str = " ".join(f'{k}="{v}"' for k, v in args.items())
        if fmt.slash_means_open:
            open_tag = f"{s}/{name} {attr_str}{e}"
        else:
            open_tag = f"{s}{name} {attr_str}{e}"

    parts = [open_tag]

    if args and fmt.arg_style == "line":
        for k, v in args.items():
            parts.append(f"{fmt.arg_prefix}{k}{fmt.arg_kv_sep}{v}")

    if body:
        parts.append(body)

    parts.append(close_tag)
    return "\n".join(parts)
