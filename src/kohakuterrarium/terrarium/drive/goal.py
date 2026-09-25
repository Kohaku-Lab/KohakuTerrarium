"""GoalDriveRegistration + the ``kind="goal"`` semantic payload (design §11.1).

This module owns pure goal-spec normalization, deterministic budget checks, and
the registration roles for schema validation, readiness, projection, and
terminal verification. It performs no delivery or persistence.

The drive core treats ``spec`` as opaque; goal semantics remain isolated in this
registration and are shared with the ``/goal`` command. Because completion
policy varies per Drive while a registration exposes one verifier mode,
terminal verification resolves the selected policy per record.
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
    """Indicate that a goal specification cannot be normalized."""


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
    """Validate a goal specification and populate all default fields."""
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
    """Build a normalized goal specification from command-style fields."""
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
    """Return the first exhausted pursuit budget without mutating state."""
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
    "rather than asserting it. Obey the goal's budgets; exhausting a budget stops "
    "continuation without completing or resuming the goal — stop and report the "
    "state so the owner can raise the budget and wake it."
)

# Per-field limits keep goal projections bounded before aggregate size checks.
_MAX_OBJECTIVE_CHARS = 240
_MAX_CRITERIA = 6


class GoalDriveRegistration:
    """Define validation, readiness, projection, and completion for goal drives."""

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
            # Completion policy belongs to each Drive, so terminal proposals must
            # route through registration-level extension verification.
            verifier_mode="extension",
        )

    def validate_spec(self, spec: dict[str, Any]) -> None:
        """Reject malformed goal specifications."""
        try:
            normalize_goal_spec(spec)
        except GoalSpecError as exc:
            raise DriveValidationError(str(exc)) from exc

    def validate_transition(self, before: Any, proposal: Any, context: Any) -> None:
        return None

    def readiness(
        self, drive: Any, dependencies: Any, now: datetime, *, turns_used: int = 0
    ) -> Readiness:
        """Compute readiness from autonomy mode and consumed turn budget."""
        spec = self._spec(drive)
        if spec.get("autonomy") != "continue_when_ready":
            # Manual goals receive one initial opportunity and require an
            # authorized wake before any continuation.
            return Readiness(
                ready=False, initial=True, reason="manual autonomy: awaiting wake"
            )
        block = budget_block_reason(spec, turns_used=turns_used)
        if block is not None:
            # Exhausted budgets stop continuation without implying completion.
            return Readiness(ready=False, reason=block)
        return Readiness(ready=True, re_arm=True)

    def project_event(
        self, drive: Any, assignment: Any, reason: Any
    ) -> DriveProjection:
        spec = self._spec(drive)
        objective = str(spec.get("objective", ""))[:_MAX_OBJECTIVE_CHARS]
        criteria = [str(c) for c in (spec.get("success_criteria") or [])][
            :_MAX_CRITERIA
        ]
        lines = [
            f"Goal ID: {getattr(drive, 'drive_id', '-')}",
            f"Goal objective: {objective}",
        ]
        if criteria:
            lines.append("Success criteria:")
            lines.extend(f"- {c}" for c in criteria)
        lines.append(
            "Act on this objective directly with your normal tools. The current "
            "objective and Goal ID are already provided here; do not call "
            "drive_status or group_drive merely to rediscover them. Use Drive "
            "tools only when reporting progress, changing state, or when missing "
            "details actually block the work. Propose completion with evidence; "
            "do not assert it."
        )
        return DriveProjection(
            event_type="drive_ready",
            prompt_override="\n".join(lines),
            context={"kind": "goal", "objective": objective},
        )

    def verify_terminal(self, proposal: Any, context: Any) -> VerificationResult:
        """Verify a terminal proposal using the goal's completion policy."""
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
        # Verifier policy uses evidence presence as its deterministic gate.
        evidence = getattr(proposal, "evidence", None) or {}
        if evidence:
            return VerificationResult(approved=True)
        return VerificationResult(
            approved=False, reason="verifier goal requires completion evidence"
        )

    def prompt_contribution(self) -> str | None:
        return _PROMPT

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
