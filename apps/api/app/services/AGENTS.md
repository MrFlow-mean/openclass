## Service-layer architecture rules

The service layer must not keep growing `chatbot.py`.

### chatbot.py rules

`chatbot.py` is an orchestrator. It may:
- create turn context
- call intent/decision/resolver/planner/executor modules
- persist history
- build responses

`chatbot.py` must not:
- add new intent regexes
- add new sequence-planning rules
- add new target-location heuristics
- add new document quality heuristics
- directly modify rich document structure except through BoardEditor/document services

### Where new logic belongs

- User phrase recognition -> `turn_intent.py`
- Board task action selection -> `board_task_decider.py`
- BoardTaskRequirementSheet normalization -> `board_task_manager.py`
- Board target location -> `segment_resolver.py` or target resolver modules
- Sequential explanation planning -> `sequence_planner.py`
- Exercise/paragraph atom splitting -> `explanation_atoms.py` or atom extractors
- Document edit safety -> board document quality gate/editor service

### Refactor rule

When touching `chatbot.py`, Codex must answer:

1. Is this new logic better placed in a smaller module?
2. Is there a fixture covering the old and new behavior?
3. Does this change reduce or increase `chatbot.py` responsibility?

If responsibility increases, stop and propose a smaller design.
