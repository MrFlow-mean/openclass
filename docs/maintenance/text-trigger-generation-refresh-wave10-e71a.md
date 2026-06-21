# Text-Trigger Generation Refresh Wave 10

Branch: `codex/prep/text-trigger-generation-refresh-wave10-e71a`

Base: `origin/main` at `e71a5c9168db37ef126ccbb8f574359f840e258f`

Scope:

- Prep-only classifier and terminal-candidate module for blank-board text-triggered generation.
- Focused tests for candidate purity, generation-control context gating, and precedence exclusions.
- No production routing, no `chatbot.py` wiring, and no shared generation handler extraction.

Refresh notes:

- `board_generation_action == "start"` is excluded so explicit API-start generation keeps its own terminal.
- `resource_reference_action`, selected resource context, and resource prompts are excluded so resource prompt and confirmed-resource generation paths keep precedence.
- Non-empty board documents are excluded so existing-board requests stay in the second-layer BoardTask workflow.
- Short generation-control text is excluded when there is no actionable requirement context, preventing unactionable "continue" turns from becoming generation candidates.

Files intentionally not touched:

- `apps/api/app/services/chatbot.py`
- `apps/api/app/services/workflow_trace.py`
- `apps/api/app/models.py`
- `apps/api/app/routers/chat.py`
- `apps/api/app/services/chat_service.py`
- Shared test helpers and fixtures

Verification:

- `/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_text_triggered_generation_candidates.py` -> 3 passed
- `/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_chat_turn_gate.py apps/api/tests/board_task/test_ready_requirement_generation_handler.py apps/api/tests/board_task/test_confirmed_resource_generation_handler.py apps/api/tests/board_task/test_confirmed_resource_generation_contract.py` -> 50 passed
- `/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_workflow_trace.py -k generation_resource_prompt` -> 5 passed, 38 deselected

Not run:

- `/Users/liqianhao/Desktop/openclass/.venv/bin/python -m ruff check apps/api/app/services/chat/paths/generation_text_trigger.py apps/api/tests/board_task/test_text_triggered_generation_candidates.py` because the shared venv does not have `ruff` installed.
