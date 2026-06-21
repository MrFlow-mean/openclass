# Text-Triggered Generation Split - Wave 9 Prep G2

Base SHA: `555e4ca8214c84f878d5488be01ebd4969db6aa7`

Branch: `codex/prep/text-triggered-generation-split-wave9-555e`

Evidence: P10 `b0bbde3be9555062a7df2519562fce90b737899f`

## Scope

This prep branch manually replays only the split proposal for blank-board
generation reached from learner text. It does not merge or cherry-pick P10.

Added proposal modules:

- `apps/api/app/services/chat/paths/generation_text_trigger.py`
- `apps/api/app/services/chat/paths/text_triggered_generation.py`

The split keeps classification separate from execution:

- `classify_text_triggered_generation_request(...)` is a side-effect-free
  classifier for blank-board text generation triggers.
- `handle_text_triggered_generation(...)` owns the audited freeze, BoardEditor
  generation, failure persistence, success commit, consume, save, and response
  assembly steps for the classified lane.

## Ownership Boundary

Owned triggers:

- `document_artifact_request`:
  `turn_intent.wants_document_artifact_generation(...)`.
- `generation_control_request`:
  `is_generation_control_request(...)` plus an existing actionable requirement
  context.

Explicitly not owned:

- `board_generation_action="start"` API generation start.
- resource confirmation turns.
- resource prompt turns.
- nonblank board documents.
- knowledge-board minimal first-board generation.
- existing-board BoardTask routing.

## Production Replay Note

This is a split proposal only. The branch intentionally does not touch
`chatbot.py`, `workflow_trace.py`, `models.py`, routers, chat service, or shared
test helpers.

When production replay is approved, wire the classifier after resource prompt
and confirmed-resource precedence, then pass its result to
`handle_text_triggered_generation(...)` with the same dependency injection shape
used by the focused direct tests.

## Verification

Focused tests:

```text
apps/api/tests/board_task/test_text_triggered_generation_split_proposal.py
```

Contracts covered:

- classifier is side-effect-free and excludes API start, resource confirmation,
  resource prompt, and nonblank board turns;
- success freezes before generate, commits, consumes, saves, responds, and
  records existing workflow trace nodes;
- failure persists a retryable frozen run without a board-generation commit.
