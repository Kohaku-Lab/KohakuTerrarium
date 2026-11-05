"""Sub-agent cells of a ``CreatureView``: a foreground sub-agent is a ◆ cell whose tool calls
nest under it (batched like the creature's own); a background one is a notice line. Mixed into
``CreatureView``, whose ``render`` dispatches ``subagent_start`` / ``subagent_tool`` /
``subagent_end`` here; the state (``_cells``, ``_children``, ``_sub_batch``, ``_closed``)
belongs to the view."""

from typing import Any

from kohakutui.ui import Cell, Markdown, Node, Text

from kohakuterrarium.builtins.ktui.format import BATCH_TITLE, one_line


class SubagentCells:
    app: Any
    doc: Any
    collapse_tools: bool
    batch_size: int
    _cells: dict[str, Node]
    _children: dict[str, list]
    _sub_batch: dict[str, Node]
    _closed: set[str]

    def _subagent_start(
        self, job_id: str, name: str, task: str, background: bool
    ) -> None:
        self._assistant_end()
        if background:
            self._notice(
                f"◆ {name}  {one_line(task, 60)} · background sub-agent", tone="agent"
            )
            return
        spec = Cell(
            role="tool",
            title=name,
            icon="◆",
            detail=one_line(task, 70) or None,
            status="running",
            tone="agent",
            collapsed=self.collapse_tools or None,
        )
        self._cells[job_id] = self.doc.add(spec)
        self._children[job_id] = []

    def _subagent_tool(
        self, job_id: str, activity: str, tool: str, detail: str
    ) -> None:
        parent = self._cells.get(job_id)
        children = self._children.get(job_id)
        if parent is None or children is None:
            return
        if activity == "start":
            spec = Cell(
                role="tool",
                title=tool,
                detail=detail or None,
                status="running",
                tone="tool",
            )
            batch = self._sub_batch.get(job_id)
            if batch is None:
                batch = parent
                if self.batch_size > 0:
                    batch = parent.add(
                        Cell(role="tool", batch=self.batch_size, title=BATCH_TITLE)
                    )
                    self._sub_batch[job_id] = batch
            children.append((tool, batch.add(spec)))
            return
        for i, (child_name, node) in enumerate(children):
            if child_name == tool:
                status = "error" if activity == "error" else "ok"
                node.update(status=status, summary=one_line(detail, 60) or None)
                node.seal()
                del children[i]
                return

    def _subagent_end(
        self, job_id: str, name: str, result: str, error: str, stats: str
    ) -> None:
        if job_id in self._closed:
            self._closed.discard(job_id)
            return
        cell = self._cells.pop(job_id, None)
        self._children.pop(job_id, None)
        self._sub_batch.pop(job_id, None)
        status = "error" if error else "ok"
        summary = one_line(error, 70) if error else stats
        if cell is None:
            self._assistant_end()
            spec = Cell(
                role="tool",
                icon="◆",
                title=name,
                status=status,
                tone="agent",
                detail=summary,
            )
            self._seal(spec)
            return
        if error:
            cell.add(Text(error, tone="danger"))
        elif result.strip():
            cell.add(Markdown(source=result))
        cell.update(status=status, summary=summary)
        cell.seal()


__all__ = ["SubagentCells"]
