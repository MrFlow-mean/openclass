## Service-layer architecture rules

The previous Chatbot/product workflow runtime has been removed on purpose.
Do not reintroduce it by restoring old route gates, board-task managers,
requirement-history runners, interaction-session handlers, or workflow traces.

### chatbot.py rules

`chatbot.py` is the temporary reset entrypoint. It may:
- load the lesson workspace context
- ask the text model for a short learner-facing reply
- record a chat commit
- clear legacy active workflow state
- build a backward-compatible `ChatResponse`

`chatbot.py` must not:
- write or edit the right-side document
- infer product workflow routes
- create new requirement or board-task state machines
- add subject, textbook, exam, or demo branches
- become the place where the next workflow architecture grows

### Where new logic belongs

The next product workflow needs a new design before code is added. Add new
modules only after naming the state object, role boundary, write authority, and
history/audit contract for that module.

Until that design lands, the default service behavior is intentionally narrow:
chat turns are recorded, documents are not mutated, and no old workflow state is
created.
