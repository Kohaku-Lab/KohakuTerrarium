"""Job-id → tool-name/label derivation shared across the runtime.

Tool job ids encode the tool name plus a short suffix (``bash_ff642767``).
The provider-safe tool name (``bash``) and the UI display label
(``bash[ff6427]``) both derive from that id, so replay (which must emit
provider-valid tool message names) and UI rendering share one source of
truth. Kept free of any KohakuTerrarium imports so low-level modules
(e.g. session.history) can use it without import cycles.
"""


def make_job_label(job_id: str) -> tuple[str, str]:
    """Extract ``(tool_name, label)`` from a job id.

    ``label`` is the human-facing display form ``tool_name[short_id]``
    (e.g. ``bash[ff6427]``); ``tool_name`` is the provider-safe name
    (``bash``).
    """
    tool_name = job_id.rsplit("_", 1)[0] if "_" in job_id else job_id
    short_id = job_id.rsplit("_", 1)[-1][:6] if "_" in job_id else ""
    label = f"{tool_name}[{short_id}]" if short_id else tool_name
    return tool_name, label
