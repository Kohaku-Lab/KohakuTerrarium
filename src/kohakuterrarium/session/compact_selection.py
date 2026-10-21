"""Choose compatible compaction segments by coverage and input inheritance."""

from kohakuterrarium.core.compact_branch import CompactRule, compact_rule_from_event

Candidate = tuple[int, int, frozenset[int], CompactRule]


def _dominates(a: Candidate, b: Candidate) -> bool:
    """Whether one summary contains another's replacement and input scope."""
    a_index, a_compat, a_coverage, a_rule = a
    b_index, b_compat, b_coverage, b_rule = b
    if a_compat < b_compat or not b_coverage <= a_coverage:
        return False
    a_inputs, b_inputs = a_rule.inputs(), b_rule.inputs()
    if b_inputs is not None and (a_inputs is None or not b_inputs <= a_inputs):
        return False
    return a_compat > b_compat or a_coverage > b_coverage or a_index > b_index


def select_compact_rules(
    events: list[dict], selected: dict[int, int]
) -> list[CompactRule]:
    """Retain non-dominated summaries compatible with the selected lineage."""
    pairs = set(selected.items())
    refs = [
        (event["event_id"], event.get("turn_index"), event.get("branch_id"))
        for event in events
        if type(event.get("event_id")) is int
        and (
            type(event.get("turn_index")) is not int
            or selected.get(event["turn_index"]) == event.get("branch_id")
        )
    ]
    candidates: list[Candidate] = []
    for index, event in enumerate(events):
        rule = compact_rule_from_event(event)
        if rule is None or (compatibility := rule.compatibility(pairs)) <= 0:
            continue
        coverage = frozenset(
            event_id
            for event_id, turn, branch in refs
            if rule.covers(event_id, turn, branch)
        )
        if coverage:
            candidates.append((index, compatibility, coverage, rule))
    kept = [
        candidate
        for candidate in candidates
        if not any(
            other is not candidate and _dominates(other, candidate)
            for other in candidates
        )
    ]
    kept.sort(key=lambda candidate: (candidate[3].replaced_from, candidate[0]))
    return [candidate[3] for candidate in kept]
