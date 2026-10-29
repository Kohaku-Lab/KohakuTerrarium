# Prepared sources for existing history mutations

History edits and default regeneration prepare an operation-owned source before
changing live context. This is an in-memory refactoring of the existing session
format; it does not introduce a stored representation ID or new recovery authority.

## Behavior

A source contains detached messages, selected turn/branch/parent coordinates,
context cutoff and compact counters. The current runtime ConversationConfig remains
shared. Callers carry the target index separately and resolve it again when replay
or retention replaces the prepared messages.

Edit preparation resolves the selected history, refreshes retained pending tools and
reconstructs affected summary inputs. Regeneration prepares the current tail by the
same underlying operations. Validation and next-branch lookup precede adoption.
The live conversation is unchanged if this preparation fails or the target is invalid.
This includes an invalid edit attempted against another branch: selection preparation
alone must not switch the live branch.

Adoption installs a second message copy. Subsequent truncation and user metadata
changes cannot alter the prepared source. Existing Conversation identity is retained
for edit/regeneration. The raw-history compatibility wrapper retains its previous
conversation-replacement behavior.

## Limits

Preparation does not make the whole operation transactional. Event-write failure or
cancellation after adoption still follows the existing runtime behavior. The canonical
identity returned by append_user_event_pair is a recorded key/event ID, not a durable
commit acknowledgement. Only default tail regeneration consumes that new return in
A1; ordinary input and edit propagation are not redesigned here.

Full-context copies are acceptable for this bounded history-operation slice. This
change makes no incremental CPU/memory or ordinary-save performance claim. Live
configuration changes and arbitrary custom copy hooks are outside the detachment
contract; message payloads and metadata are copied using the existing Python objects.

Tests cover invalid selected-branch targets, nested source/adoption isolation, existing
branch-elision parity, settled-tool refresh, and failure after refresh before adoption.
Existing history integration continues through edit, regeneration, rewind and restore.
