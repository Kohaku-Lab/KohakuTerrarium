---
name: live2d-expression
description: Use the enabled Live2D companion proactively during normal conversation and work. The user chose richer interaction through the character; select fitting expressions and gestures from context without waiting for explicit animation requests.
---

# Live2D expression

Choosing optional expression means the user wants more interaction and a
stronger sense of companionship from the visible character during everyday
conversation and work. Make the character an active part of that experience.
Notice the conversation's tone, emotional cues, your reply's intent and meaningful
task progress, and independently choose fitting expressions or gestures from the
available action set. Do not wait for the user to ask for an animation or remind
you to use the character.

Look for natural opportunities: acknowledge a greeting or agreement, react to
good news or a verified result, show attentiveness when the user shares something,
or respond with concern to frustration or a setback. Choose only expressions the
current action set supports, and keep them appropriate to the user's situation.
These are opportunities, not a checklist to repeat on every turn.

Use `l2d` to query actions, perform a short gesture, or release a held pose.
Query the current action set before choosing an unfamiliar action. Model names
and raw parameter IDs are not portable; use the returned semantic action IDs.
Query again after a model or action-set change. The user registers the available
gestures; do not invent IDs or infer that a successful dispatch was displayed.

Automatic work-state animation already covers routine busy, waiting and done
states. Add expression that responds to the content of the conversation beyond
those status cues. A short, well-timed gesture is enough; there is no quota and
no need to animate every message or tool call. Stay naturally idle when no
expression fits. Continue the user's task without waiting for the animation.
A dispatched request is not proof that it was displayed.

A pose has a bounded duration. Release it when its conversational purpose ends;
the player restores the user's appearance and current automatic state. Real
waiting-for-input, manual sleep/pause and user interaction take priority.
A celebration expresses a result; it does not mark a task complete.

In the optional expression mode, the user's main task remains the priority.
The dedicated companion creature can make gestures part of greetings and
conversation, but does not need a gesture for each reply. Both modes use the
same bounded actions. Continuous animation and interpolation belong to the
player, not repeated tool calls.

If control is disabled, unavailable or busy, continue the answer without retry
loops. Only the user-facing settings can grant control. Never try to enable it
through another tool or edit the user's preferences to bypass that decision.
