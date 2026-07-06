## Service-layer architecture rules

### chatbot.py rules

`chatbot.py` is the service-layer routing entrypoint. It may:
- load the lesson workspace context
- delegate to the current workflow modules
- record chat and lesson commits
- build a backward-compatible `ChatResponse`

`chatbot.py` must not:
- write or edit the right-side document directly
- absorb product workflow execution details
- add subject, textbook, exam, or demo branches
- become the place where workflow architecture keeps growing

### Where new logic belongs

Add new workflow modules only after naming the state object, role boundary,
write authority, and history/audit contract for that module. Keep long-lived
behavior in focused services instead of growing `chatbot.py`.
