# Initial Learning Intent Contract

This contract covers the first gate for an empty `board_document`. It is an architecture contract for future implementation, not a claim that every part is currently wired.

## Purpose

When the board is empty, OpenClass must not send every learner message directly into a full requirement sheet or a default course template. It first decides the general learning shape and whether the goal is narrow enough for a first board.

The gate solves only generic learning-mode and goal-granularity problems. It must not depend on subject, textbook, exam, demo, or single-example keywords.

## Inputs

Allowed:

- current user message
- lightweight lesson / course metadata relevant to the learning goal
- existing, not-yet-frozen first-layer requirement state

Forbidden:

- board full text, because this route is only for an empty board
- BoardEditor calls
- direct board-content generation

## Output

The gate returns structured state:

- `learning_mode`: `learn_concept`, `practice_activity`, or `undecided`
- `target_granularity`: `specific_concept`, `broad_domain`, or `ambiguous`
- `next_action`: `freeze_minimal_and_generate_board`, `ask_specific_concept`, `collect_practice_requirements`, or `ask_learning_mode`
- `readiness.goal_shape`: `atomic_concept`, `bounded_question`, `bounded_task_slice`, `underbounded_process`, `broad_domain`, `practice_activity`, or `ambiguous`
- `readiness.readiness_for_initial_board`: `ready`, `needs_narrowing`, `needs_practice_requirements`, or `needs_learning_mode`
- `readiness.missing_boundaries`: generic missing boundaries such as object, task scenario, constraints, or learning mode
- `trace_reason`: generic reason for the chosen action and rejected alternatives

## Transitions

### Specific Knowledge Goal

When `learning_mode=learn_concept` and `target_granularity=specific_concept`:

1. Requirement Manager creates a `minimal frozen requirement`.
2. It writes ready / frozen versions and events.
3. BoardEditor consumes only the frozen payload.
4. A successful generation writes a lesson commit with gate output and requirement version metadata.
5. Chatbot only acknowledges and carries the user forward.

### Broad Knowledge Goal

When the user gives a broad domain:

- do not generate a default learning path
- do not ask practice-only fields unless the user requested practice
- ask for one narrower concept, question, scope, object, task scenario, or constraint

### Practice Activity

When `learning_mode=practice_activity`:

- enter practice-oriented requirement collection
- collect practice content, level, goal scenario, practice form, feedback style, and success criteria
- generate only when the sheet is ready or the user explicitly forces start, with frozen history

### Undecided

When mode or granularity is unreliable, ask whether the user wants knowledge learning or practice-style learning before moving on.

## Role Boundary

- Chatbot asks, clarifies, and acknowledges; it never generates right-board content.
- Requirement Manager owns sheets, gate state, versions, and events.
- BoardEditor runs only after receiving a frozen requirement payload.
- Prompt may help classify, but cannot replace gate state, version history, frozen snapshots, or commit metadata.

## Trace And Tests

Any implementation must write response or commit metadata containing:

- `learning_mode`
- `target_granularity`
- `next_action`
- `trace_reason`
- `requirement_phase`
- whether a `minimal frozen requirement` was created
- whether BoardEditor was called

Before default-route integration, add golden fixtures. Each positive example needs at least two negative examples. Fixtures must express generic learning shapes, not subject or demo-specific wording.
