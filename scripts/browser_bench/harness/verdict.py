"""The agent's machine-readable verdict: the system prompt that asks for it and its parser.

The final message ends with ``RESULT: DONE`` or ``RESULT: NOT DONE`` and,
when the request asks for a value, ``ANSWER: <value>``.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

DONE = "done"
NOT_DONE = "not_done"
MISSING = "missing"
MALFORMED = "malformed"

SYSTEM_PROMPT = """\
You operate a web browser on the user's behalf. Carry out the user's request \
in the browser with the browser tools.

End your final message with a result line, exactly one of:

RESULT: DONE
RESULT: NOT DONE

Write `RESULT: DONE` only when the request has actually been carried out. \
When you could not carry it out, or it cannot be done, write \
`RESULT: NOT DONE` and say why in one sentence just above that line.

When the request asks you to find or report a value, add one more line \
after the result line with just that value:

ANSWER: <value>
"""

_DECORATION = re.compile(r"[*`]")
_RESULT = re.compile(r"^RESULT\s*:\s*(.*)$", re.IGNORECASE)
_ANSWER = re.compile(r"^ANSWER\s*:\s*(.*)$", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Verdict:
    """What the final message says about the run: status, answer, the result line."""

    status: str
    answer: str | None = None
    line: str = ""

    @property
    def claimed(self) -> bool:
        return self.status == DONE

    @property
    def refused(self) -> bool:
        return self.status == NOT_DONE

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "answer": self.answer, "line": self.line}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Verdict":
        return cls(data["status"], data.get("answer"), data.get("line", ""))


def _clean(line: str) -> str:
    return _DECORATION.sub("", line).strip().lstrip(">-_").rstrip("_").strip()


def _status_of(value: str) -> str:
    words = re.sub(r"[\s_-]+", " ", value.strip().rstrip(".!").strip()).upper()
    match words:
        case "DONE":
            return DONE
        case "NOT DONE":
            return NOT_DONE
        case _:
            return MALFORMED


def parse_verdict(text: str | None) -> Verdict:
    """The last ``RESULT:`` line's status and the last ``ANSWER:`` line's value."""
    status, line, answer = MISSING, "", None
    for raw in (text or "").splitlines():
        cleaned = _clean(raw)
        if match := _RESULT.match(cleaned):
            status, line = _status_of(match.group(1)), raw.strip()
        elif match := _ANSWER.match(cleaned):
            answer = match.group(1).strip() or None
    return Verdict(status, answer, line)


def _numbers(text: str) -> list[Decimal]:
    out = []
    for token in _NUMBER.findall(text):
        try:
            out.append(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return out


def answer_matches(expected: str, given: str | None) -> bool:
    """Whether ``given`` states ``expected``: numerically when it is a number, else as text."""
    if not given:
        return False
    wanted = _numbers(expected)
    if wanted:
        return wanted[0] in _numbers(given)
    return _words(expected) in _words(given)


def _words(text: str) -> str:
    return " ".join(text.lower().split())


def user_message(instruction: str, url: str) -> str:
    """The user turn: the task instruction and the page it starts from."""
    return f"{instruction}\n\nThe page: {url}"
