"""GoalDriveRegistration + the ``kind="goal"`` semantic payload (design §11.1).

Ships the deterministic Goal-kind policy beside the builtin generic registration:
pure, framework-independent validation + normalization of the Goal spec plus a
deterministic budget check, and the registration that answers the Drive core's
deterministic questions (schema / readiness / projection / terminal
verification). It runs no LLM, writes no repository, and dispatches no events.

The Drive core never interprets ``spec``; the spec helpers here are what the
Goal registration and the ``/goal`` command agree on. The design's "no
/goal-specific field in the core generic model" holds — this is a REGISTRATION
beside generic, not a model change.

Verifier note: the Drive core reads one verifier *mode* from a registration's
descriptor for all its records. Goal completion is per-Drive (self_propose /
user_confirm / verifier), so this registration declares ``extension`` mode and
resolves the actual per-Drive policy inside :meth:`verify_terminal`.
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.registration import (
    DriveProjection,
    DriveRegistrationDescriptor,
    Readiness,
    VerificationResult,
)

COMPLETION_POLICIES: frozenset[str] = frozenset(
    {"self_propose", "user_confirm", "verifier"}
)
AUTONOMY_MODES: frozenset[str] = frozenset({"manual", "continue_when_ready"})
BUDGET_KEYS: tuple[str, ...] = ("max_turns", "max_tool_calls", "max_walltime_s")

DEFAULT_COMPLETION_POLICY = "self_propose"
DEFAULT_AUTONOMY = "manual"


class GoalSpecError(ValueError):
    """A malformed GoalSpec. Raised by :func:`normalize_goal_spec`."""


def _str_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raise GoalSpecError(f"{name} must be a list of strings, not a bare string")
    if not isinstance(value, (list, tuple)):
        raise GoalSpecError(f"{name} must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise GoalSpecError(f"{name} entries must be non-empty strings")
        out.append(item.strip())
    return out


def _budgets(value: Any) -> dict[str, int | None]:
    if value is None:
        return {key: None for key in BUDGET_KEYS}
    if not isinstance(value, dict):
        raise GoalSpecError("budgets must be an object")
    out: dict[str, int | None] = {}
    for key in BUDGET_KEYS:
        raw = value.get(key)
        if raw is None:
            out[key] = None
            continue
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise GoalSpecError(f"budgets.{key} must be a positive integer or null")
        out[key] = raw
    unknown = set(value) - set(BUDGET_KEYS)
    if unknown:
        raise GoalSpecError(f"unknown budget keys: {sorted(unknown)}")
    return out


def normalize_goal_spec(spec: Any) -> dict[str, Any]:
    """Validate ``spec`` and return a fully-populated, normalized GoalSpec.

    Raises :class:`GoalSpecError` on any malformed field. The objective is the
    only required field; every other field defaults conservatively (manual
    autonomy, self-propose completion, no budgets).
    """
    if not isinstance(spec, dict):
        raise GoalSpecError("goal spec must be an object")
    objective = spec.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise GoalSpecError("goal spec requires a non-empty 'objective'")

    completion = spec.get("completion_policy", DEFAULT_COMPLETION_POLICY)
    if completion not in COMPLETION_POLICIES:
        raise GoalSpecError(
            f"completion_policy must be one of {sorted(COMPLETION_POLICIES)}"
        )
    autonomy = spec.get("autonomy", DEFAULT_AUTONOMY)
    if autonomy not in AUTONOMY_MODES:
        raise GoalSpecError(f"autonomy must be one of {sorted(AUTONOMY_MODES)}")

    return {
        "objective": objective.strip(),
        "success_criteria": _str_list(spec.get("success_criteria"), "success_criteria"),
        "constraints": _str_list(spec.get("constraints"), "constraints"),
        "completion_policy": completion,
        "autonomy": autonomy,
        "budgets": _budgets(spec.get("budgets")),
    }


def build_goal_spec(
    objective: str,
    *,
    success_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    completion_policy: str = DEFAULT_COMPLETION_POLICY,
    autonomy: str = DEFAULT_AUTONOMY,
    budgets: dict[str, int | None] | None = None,
) -> dict[str, Any]:
    """Construct a normalized GoalSpec from keyword parts (used by ``/goal set``)."""
    return normalize_goal_spec(
        {
            "objective": objective,
            "success_criteria": success_criteria or [],
            "constraints": constraints or [],
            "completion_policy": completion_policy,
            "autonomy": autonomy,
            "budgets": budgets or {},
        }
    )


def budget_block_reason(
    spec: dict[str, Any],
    *,
    turns_used: int = 0,
    tool_calls_used: int = 0,
    walltime_s: float = 0.0,
) -> str | None:
    """Deterministic budget verdict (design §11: budgets pause/block, never
    complete).

    Returns a human-readable reason string when any configured budget is
    exhausted, else ``None``. The counters are supplied by the caller (the
    continuation policy derives ``turns_used`` from the Drive's delivery
    count); this function never mutates state and never proposes completion.
    """
    budgets = spec.get("budgets") or {}
    max_turns = budgets.get("max_turns")
    if max_turns is not None and turns_used >= max_turns:
        return f"turn budget exhausted ({turns_used}/{max_turns})"
    max_tool_calls = budgets.get("max_tool_calls")
    if max_tool_calls is not None and tool_calls_used >= max_tool_calls:
        return f"tool-call budget exhausted ({tool_calls_used}/{max_tool_calls})"
    max_walltime = budgets.get("max_walltime_s")
    if max_walltime is not None and walltime_s >= max_walltime:
        return f"walltime budget exhausted ({walltime_s:.0f}s/{max_walltime}s)"
    return None


_PROMPT = (
    "Goal drives carry a durable objective. Pursue the stated objective as a "
    "continuing commitment — never invent a new objective. On a recovery event, "
    "inspect the current world before repeating any side effect. Report material "
    "progress (or blocking) with evidence, and propose completion with evidence "
    "rather than asserting it. Obey the goal's budgets and wait/pause policy; if a "
    "budget is exhausted, propose pausing, never completing."
)

# Bounded projection budget: the objective line is truncated to keep the
# per-event context small (the core also caps the projected context dict).
_MAX_OBJECTIVE_CHARS = 240
_MAX_CRITERIA = 6


class GoalDriveRegistration:
    """Durable objective-pursuit policy for Drive ``kind="goal"``."""

    name = "goal"
    kind = "goal"
    schema_version = 1
    description = "Durable objective pursuit policy."

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=self.schema_version,
            description=self.description,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            prompt_contribution=_PROMPT,
            # Per-Drive completion policy is enforced in verify_terminal; the
            # core still routes every terminal proposal through it (§4.2).
            verifier_mode="extension",
        )

    # -- schema --------------------------------------------------------------

    def validate_spec(self, spec: dict[str, Any]) -> None:
        """Fail closed on a malformed GoalSpec (design §8.8)."""
        try:
            normalize_goal_spec(spec)
        except GoalSpecError as exc:
            raise DriveValidationError(str(exc)) from exc

    def validate_transition(self, before: Any, proposal: Any, context: Any) -> None:
        # Generic edges (drive_policy) already forbid terminal reopen; Goal adds
        # no extra transition constraints beyond its terminal verification.
        return None

    # -- readiness -----------------------------------------------------------

    def readiness(
        self, drive: Any, dependencies: Any, now: datetime, *, turns_used: int = 0
    ) -> Readiness:
        """Autonomy- and budget-aware readiness (design §11.2, §11.4).

        ``manual`` goals never auto-re-arm (an authorized actor must wake them).
        ``continue_when_ready`` goals re-arm after each prior settlement — the
        ``re_arm`` signal drives the generic dispatcher's continuation — until a
        configured budget is exhausted, at which point they stop re-arming
        without ever completing (budgets pause/block, never succeed). The
        manager supplies ``turns_used`` (the settled-delivery count).
        """
        spec = self._spec(drive)
        if spec.get("autonomy") != "continue_when_ready":
            # Manual autonomy: grant exactly one initial delivery (design §11.4
            # `/goal set` -> initial event) but never auto-re-arm; continuation
            # requires an explicit authorized wake.
            return Readiness(
                ready=False, initial=True, reason="manual autonomy: awaiting wake"
            )
        block = budget_block_reason(spec, turns_used=turns_used)
        if block is not None:
            # Budget exhausted: stop continuation; the creature proposes a
            # pause/block, and completion is never inferred from exhaustion.
            return Readiness(ready=False, reason=block)
        return Readiness(ready=True, re_arm=True)

    # -- projection ----------------------------------------------------------

    def project_event(
        self, drive: Any, assignment: Any, reason: Any
    ) -> DriveProjection:
        spec = self._spec(drive)
        objective = str(spec.get("objective", ""))[:_MAX_OBJECTIVE_CHARS]
        criteria = [str(c) for c in (spec.get("success_criteria") or [])][
            :_MAX_CRITERIA
        ]
        lines = [f"Goal objective: {objective}"]
        if criteria:
            lines.append("Success criteria:")
            lines.extend(f"- {c}" for c in criteria)
        lines.append(
            "This is a continuing commitment. Report progress with evidence and "
            "propose completion with evidence; do not assert it."
        )
        return DriveProjection(
            event_type="drive_ready",
            prompt_override="\n".join(lines),
            context={"kind": "goal", "objective": objective},
        )

    # -- terminal verification -----------------------------------------------

    def verify_terminal(self, proposal: Any, context: Any) -> VerificationResult:
        """Per-Drive completion policy (design §11.1).

        * ``self_propose`` — an authorized proposal is accepted.
        * ``user_confirm`` — only a user-actor proposal finalizes; a creature's
          proposal is not accepted, so completion stays with the human
          ``/goal complete`` path.
        * ``verifier`` — a deterministic evidence gate: the proposal must carry
          non-empty evidence.

        Budget exhaustion never appears here: budgets pause/block a goal, they
        never drive it to completed (design §11).
        """
        record = (context or {}).get("record") if isinstance(context, dict) else None
        spec = self._spec(record) if record is not None else {}
        policy = spec.get("completion_policy", "self_propose")
        if policy == "self_propose":
            return VerificationResult(approved=True)
        if policy == "user_confirm":
            proposer = getattr(proposal, "proposed_by", None)
            if getattr(proposer, "kind", None) == "user":
                return VerificationResult(approved=True)
            return VerificationResult(
                approved=False,
                reason="user_confirm goal: awaiting user confirmation",
            )
        # verifier policy: require deterministic evidence.
        evidence = getattr(proposal, "evidence", None) or {}
        if evidence:
            return VerificationResult(approved=True)
        return VerificationResult(
            approved=False, reason="verifier goal requires completion evidence"
        )

    def prompt_contribution(self) -> str | None:
        return _PROMPT

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _spec(drive: Any) -> dict[str, Any]:
        spec = getattr(drive, "spec", None)
        return spec if isinstance(spec, dict) else {}


__all__ = [
    "AUTONOMY_MODES",
    "BUDGET_KEYS",
    "COMPLETION_POLICIES",
    "DEFAULT_AUTONOMY",
    "DEFAULT_COMPLETION_POLICY",
    "GoalDriveRegistration",
    "GoalSpecError",
    "budget_block_reason",
    "build_goal_spec",
    "normalize_goal_spec",
]
