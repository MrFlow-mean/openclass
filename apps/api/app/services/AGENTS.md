## Service Layer Rules

`chatbot.py` 是薄编排器，不是继续堆规则的地方。

It may:

- create turn context
- call intent / decision / resolver / planner / executor modules
- persist history
- build responses

It must not:

- add new intent regexes
- add new sequence-planning rules
- add new target-location heuristics
- add new document quality heuristics
- directly modify rich document structure except through BoardEditor / document services

New logic belongs in the smallest responsible module:

- user phrase recognition -> `turn_intent.py`
- blank-board learning mode and target granularity -> `initial_learning_intent.py`
- board task action selection -> `board_task_decider.py`
- BoardTaskRequirementSheet normalization -> `board_task_manager.py`
- target location -> `segment_resolver.py` or target resolver modules
- sequence planning -> `sequence_planner.py`
- exercise / paragraph atom splitting -> `explanation_atoms.py` or atom extractors
- document edit safety -> board document quality gate / editor service

When touching `chatbot.py`, answer:

1. Is this logic better placed in a smaller module?
2. Is there a fixture covering old and new behavior?
3. Does this reduce or increase `chatbot.py` responsibility?

If responsibility increases, stop and propose a smaller design.
