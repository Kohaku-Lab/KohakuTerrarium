# Graph sidebar model switching

## Intent

Issue: https://github.com/Kohaku-Lab/KohakuTerrarium/issues/557.

A user viewing a creature in the relationship graph should be able to change its model in the right-hand details sidebar without navigating to Chat. The user explicitly excluded font-size work and authorized proceeding without further design questions.

## Scope

- Replace the selected creature's static model value with an accessible, clearly editable model picker using the existing shared model-selection drawer.
- Preserve profile, preset, and variation selection already supported by Chat.
- Route changes to the selected runtime creature's session ID and immutable creature ID, never the active chat target or a default configuration.
- Read back the graph after success; display canonical model names, including variations.
- Cancel without mutation, show backend failures, prevent duplicate submissions, and keep sample graphs read-only.
- Invalidate an open picker or pending result when its creature/session/runtime owner changes or disappears. A late result must never update a different selection.
- No new API, general configuration editor, fonts, layout redesign, or unrelated fixes.

## Implementation choices

Use Vue 3 and JavaScript, the existing `ModelSwitcherShared.vue` and its injected context, and a graph-local adapter mounted in `CreatureDetails.vue`. This choice is implementation judgment, not an additional user requirement. Keep existing graph colors, typography, and spacing; the existing model pill supplies the affordance and drawer. Do not add a second model-editor implementation.

The adapter receives the creature and graph view, captures runtime/session/creature ownership across asynchronous operations, and uses `terrariumAPI.switchCreatureModel`. It refreshes the shared graph feed after a successful request. The feed also validates runtime ownership before publishing an asynchronous snapshot, so a late readback cannot overwrite another host or user's graph. Readback failure is distinguishable from switch failure. A key tied to creature/session identity prevents a draft from moving to another creature.

## Verification

Add negative-case-focused Vue tests exercising the actual shared picker with API I/O seams. Extend the existing API runtime-graph workflow with ID-targeted switching and readback showing another creature unchanged. Run frontend tests, formatting, build, Python lint, unit/integration, and local e2e. Record baseline/environment failures rather than widening this PR into unrelated repairs.
