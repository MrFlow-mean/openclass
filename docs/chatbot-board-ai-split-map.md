# Chatbot / Board AI Split Map

## Current chat_turn_orchestrator.py responsibilities

- Turn orchestration: load workspace, choose the active flow, return `ChatResponse`.
- Runtime wiring for handlers and legacy helper callbacks that have not yet moved to stable modules.
- Interaction flow handoff: start and continue `InteractionSession`.
- Top-level route ordering across interaction, existing-board task, resource reference, initial board generation, teaching, direct edit, and free chat.

## Keep in chatbot.py

- Chatbot role reply generation only: normal chat, board-directed explanation wording, focus clarification wording, board-task clarification wording, and post-board-generation confirmation wording.
- No workspace, persistence, board editor, resolver, handler dispatch, route order, or history ownership.
- `chatbot.py` is now a role engine consumed by the orchestrator and handlers.

## Extracted or extractable groups

- Extracted: `chat/context.py` owns board/resource/conversation/selection context helpers.
- Extracted: `chat/strong_reasoning.py` owns hidden solver-context preparation.
- Extracted: `chat/resource_reference_flow.py` owns resource reference prompt responses.
- Extracted: `chat/handlers/initial_board.py` owns all first-board `generate_from_requirements` execution paths through `run_initial_board_generation`.
- Extracted: `chat/handlers/existing_board_task.py` owns existing-board task orchestration, including task sheet update, focus resolution, route decision, focus clarification, write confirmation, sequence start, and route dispatch.
- Extracted: `chat_turn_orchestrator.py` owns the former `chatbot.py` turn orchestration entrypoints.
- Extracted: `chatbot.py` is reduced to Chatbot role-message APIs.
- Existing handler seams: `chat/handlers/interaction.py`, `edit_blackboard.py`, `explain.py`, `board_task.py`, `general_chat.py`.
- Next cleanup target: reduce `chat_turn_orchestrator.py` runtime callback surfaces by moving stable helpers into owned modules.

## Role boundaries

- Chatbot: learner-visible replies, status handoff, authorized explanation or interaction.
- BoardEditor: board generation, append, rewrite, simplify, and deterministic document update output.
- FocusResolver: target location and candidate focus resolution.
- ResourceResolver: resource/chapter evidence selection only.
- BoardExplanationGate: authorization and boundary for Chatbot explanations.
- InteractionSession: rule-based role-play, practice, and turn decisions.

## Recommended PR order

1. Done: fix duplicate interaction reply ordering.
2. Done: extract context helpers.
3. Done: extract strong reasoning helper.
4. Done: extract resource reference flow.
5. Done: extract remaining initial board generation branches.
6. Done: extract existing-board task orchestration and side-effect response blocks.
7. Done: move turn orchestration from `chatbot.py` to `chat_turn_orchestrator.py`.
8. Next: reduce `ExistingBoardTaskRuntime` and `InitialBoardRuntime` callback surfaces by moving stable helpers into owned modules.

## Risks

- Moving route execution before route decision can reintroduce duplicate visible Chatbot replies.
- Handler extraction must preserve commit metadata keys and run/version history.
- Board AI handler changes need golden fixture or targeted tests with at least one negative case.
