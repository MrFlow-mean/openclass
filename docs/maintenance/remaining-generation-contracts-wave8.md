# Wave 8 Prep: Remaining Generation Path Contracts

Base SHA: `c413a192e7805df95b14b86809afe661d5721dd1`

Branch: `codex/prep/remaining-generation-contracts-wave8-c413`

Scope: prep-only audit, docs, and characterization tests for the remaining
initial board generation paths. This does not create a common generation engine,
does not extract production wiring from `chatbot.py`, and does not change
confirmed-resource generation.

## Pre-Change Classification

Request:

- Audit and prepare contracts for explicit `board_generation_action=start`,
  explicit generation-control requests, and `knowledge_board` minimal
  generation.

Files changed:

- `docs/maintenance/remaining-generation-contracts-wave8.md`
- `apps/api/tests/board_task/test_remaining_generation_contracts_wave8.py`

Problem category:

- [x] Generic product capability
- [ ] Content-shape abstraction
- [ ] Prompt quality
- [x] Schema / data structure
- [ ] UI interaction
- [ ] Resource parsing
- [ ] Specific textbook adapter
- [x] Test fixture

Forbidden content check:

- [ ] Subject keywords
- [ ] Textbook keywords
- [ ] Fixed HTML
- [ ] Fixed lecture content
- [ ] Demo content
- [ ] Single-sample production branch

## Owned Symbols

Prep-owned symbols are limited to the new test module and this document:

- `test_explicit_board_generation_action_start_contract_freezes_before_board_editor`
- `test_generation_control_contract_forces_current_requirement_without_chatbot_board_content`
- `test_knowledge_board_minimal_contract_stays_separate_from_start_metadata`
- helper symbols inside `test_remaining_generation_contracts_wave8.py`

Existing production symbols audited but not owned:

- `ChatRequest.board_generation_action`
- `BoardGenerationAction`
- `decide_chat_turn`
- `_handle_initial_learning_work_mode`
- `_generate_board_from_confirmed_resource`
- `_prepare_initial_requirement_for_board_generation`
- `_checkpoint_initial_requirement_before_generation`
- `_should_generate_board_from_explicit_request`
- `handle_ready_requirement_generation`
- `generate_from_requirements`
- `LearningRequirementHistoryRecorder.freeze`
- `LearningRequirementHistoryRecorder.consume`

## Contract Matrix

| Path | Trigger | Current entry point | Requirement source | BoardEditor input contract | Metadata contract | Confirmed-resource boundary |
|---|---|---|---|---|---|---|
| Explicit API start | `ChatRequest.board_generation_action == "start"` on a blank board, after current initial work-mode attempt returns no response | `_chat_response` explicit start branch | Latest active requirement sheet plus latest clarification, normalized by `_prepare_initial_requirement_for_board_generation` | `generate_from_requirements` receives frozen requirement IDs and no raw conversation/user instruction field | `kind=board_document_generation`, `board_generation_action=start`, `task_requirement_sheet.action_type=generate_board`, `requirement_run_status_after_commit=consumed` on success | Must not be folded into `resource_reference_confirm`; resource preflight direct-reference is disabled for API start |
| Explicit generation-control request | Text recognized by `is_generation_control_request`, such as "directly generate" intent without API field | `_chat_response` generation-control block after current initial work-mode attempt returns no response | Requirement manager output for the current turn, then forced-normalized when still incomplete but actionable | `generate_from_requirements` receives frozen requirement IDs; Chatbot must not generate board-like content | `kind=board_document_generation`, `board_generation_action=explicit_board_request`, `board_editor_message` preserves the editor result, `requirement_cleared=True` on success | If resource preflight asks for confirmation first, confirmed-resource remains a separate path |
| `knowledge_board` minimal generation | Blank board, initial work-mode classifier returns `work_mode=knowledge_board` | `_handle_initial_learning_work_mode` | Minimal requirement built from `InitialLearningWorkModeDecision` and latest user message | `generate_from_requirements` receives minimal frozen requirement IDs; no resource context unless separately selected | `kind=board_document_generation`, `board_generation_action=knowledge_board_minimal_requirement`, `initial_learning_work_mode.work_mode=knowledge_board`, `task_requirement_sheet.work_mode=knowledge_board` | Does not handle confirmed-resource generation; selected/reference prompts cause this path to return `None` |
| Confirmed-resource generation | `resource_reference_action=confirm` plus selected reference and generation-eligible message | `_generate_board_from_confirmed_resource` | Requirement manager output plus confirmed `ResourceReferenceContext` | `generate_from_requirements` receives `reference_context=resource_resolution.selected_reference` | `board_generation_action=resource_reference_confirm`, `resource_backed_generation=True` | Separate production path; this prep only marks it as separate |

## Trace Order

Current shared top-level order at `c413a192`:

1. `CONTEXT_LOAD`
2. `TURN_CONTEXT_BUILD`
3. `BOARD_ACTION_DECIDE`
4. `CHAT_TURN_GATE`
5. `RESOURCE_PREFLIGHT`
6. `ACTIVE_INTERACTION_CHECK`
7. Existing-board task flow, when applicable
8. `_handle_initial_learning_work_mode`
9. Explicit `board_generation_action=start`
10. Legacy teaching/direct-edit/document paths
11. Generation-control/document-artifact block
12. Resource prompt and confirmed-resource paths
13. Fallback explanation, ordinary chat, and ready requirement generation

Important current precedence:

- The gate classifies API start before resource reference and generation-control
  text.
- The top-level implementation still gives `_handle_initial_learning_work_mode`
  a chance before the explicit API start and generation-control branches. If the
  classifier returns `knowledge_board`, current metadata is
  `knowledge_board_minimal_requirement`, not `start` or
  `explicit_board_request`.
- Ready requirement generation has explicit trace nodes through
  `handle_ready_requirement_generation`; the three paths in this prep still do
  not all emit equivalent `INITIAL_*` workflow nodes.

## Persistence Order

All three generation paths must preserve this durable order:

1. Build or normalize a `LearningRequirementSheet`.
2. Freeze the requirement run using
   `_prepare_initial_requirement_for_board_generation`.
3. Persist the frozen checkpoint with
   `_checkpoint_initial_requirement_before_generation`.
4. Call `generate_from_requirements`.
5. On success, refresh lesson runtime, build teaching guide, commit the board
   document, consume the requirement run, clear active requirements, normalize
   package state, and save.
6. On failure, record `generation_failed`, keep the frozen run retryable, do not
   write a board commit, normalize package state, and save.

Expected event shapes:

- API start with no prior ready run: `created -> forced_frozen -> consumed` on
  success.
- Generation-control forced start: `created -> forced_frozen -> consumed` on
  success.
- `knowledge_board` minimal generation: `created -> frozen -> consumed` on
  success.
- Ready requirement generation remains separate and keeps
  `created -> completed -> frozen -> consumed`.
- Confirmed-resource generation remains separate and must preserve a confirmed
  resource context.

## Metadata Contract

Common success metadata:

- `kind=board_document_generation`
- `user_message`
- `assistant_message`
- `assistant_message_source` from the post-generation reply when available,
  otherwise from the board editor
- `board_editor_message`
- `board_edit_operation`
- `board_edit_summary`
- `board_section_titles`
- quality metadata from the board document editor
- requirement history metadata with consumed status
- task metadata with `requirement_cleared=True`

Path-specific metadata:

- API start: `board_generation_action=start`
- Generation-control: `board_generation_action=explicit_board_request`
- `knowledge_board`: `board_generation_action=knowledge_board_minimal_requirement`
  plus `initial_learning_work_mode`
- Confirmed-resource: `board_generation_action=resource_reference_confirm`,
  `resource_backed_generation=True`, and reference metadata

## Tests

New prep tests:

- `test_explicit_board_generation_action_start_contract_freezes_before_board_editor`
- `test_generation_control_contract_forces_current_requirement_without_chatbot_board_content`
- `test_knowledge_board_minimal_contract_stays_separate_from_start_metadata`

Existing related tests that should remain green:

- `apps/api/tests/board_task/test_ready_requirement_generation_trace.py`
- `apps/api/tests/board_task/test_ready_requirement_generation_handler.py`
- `apps/api/tests/board_task/test_chat_turn_gate.py`
- `apps/api/tests/test_ai_logging.py` relevant generation tests

## Risks

- Explicit API start has a gate-level route of `initial_board_generation`, but
  the implementation can still be preempted by `knowledge_board` minimal
  generation because `_handle_initial_learning_work_mode` runs first.
- API start and generation-control success paths still lack the full
  `INITIAL_REQUIREMENT_READY/FREEZE/BOARD_GENERATE/BOARD_COMMIT` trace contract
  that ready requirement generation now has.
- Several generation branches duplicate freeze, BoardEditor, commit, consume,
  and save ordering. This prep intentionally documents that duplication instead
  of removing it.
- Confirmed-resource generation remains adjacent and easy to disturb because it
  uses the same freeze and BoardEditor functions with a resource context.

## Expected Conflicts

- Future extraction of API start may conflict with work that moves
  `_handle_initial_learning_work_mode` or changes its precedence.
- Future extraction of generation-control may conflict with resource prompt
  activation or confirmed-resource trace activation.
- Any production branch that changes `board_generation_action` metadata values
  must update this contract and the characterization tests together.
- Trace activation work may add `INITIAL_*` nodes; tests that currently document
  missing trace scope should be intentionally revised in that production PR.

## Recommended Production Order

1. Extract explicit `board_generation_action=start` into its own handler first,
   because it is an API/schema-backed user control and should have the clearest
   precedence.
2. Add trace nodes and tests for API start without changing metadata values.
3. Extract generation-control request handling after API start is stable.
4. Extract `knowledge_board` minimal generation last, because its current
   precedence relative to API start is subtle and should be decided explicitly.
5. Keep confirmed-resource generation separate until its trace activation work is
   ready; do not fold it into a generic generation engine in the same PR.
