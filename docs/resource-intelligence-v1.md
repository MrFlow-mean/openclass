# Resource Intelligence v1 Design

## Product goal

Give OpenClass a generic file-material intelligence layer that can parse uploaded resources, preserve source structure, retrieve relevant evidence, and let Chatbot or BoardEditor use only explicitly selected resource context.

## User flows

- Upload PDF, DOCX, Markdown, or TXT into a course package.
- Parse the file into chapters, sections, pages/blocks, chunks, and source metadata.
- Ask a question or request board generation with resource language such as "根据资料".
- ResourceResolver proposes or selects a relevant chapter/section.
- Chatbot answers only with selected resource evidence, or BoardEditor generates/updates board content from selected evidence.

## Backend model proposal

- Keep raw resource files and extracted blocks separate from lesson board snapshots.
- Store derived chunks with resource id, chapter id, page/block locator, heading path, text hash, and extraction quality.
- Treat embeddings/FTS indexes as derived search layers, not as the truth source.

## API proposal

- Preserve current chat payload shape for v1 unless a separate resource-management endpoint needs explicit design.
- Resource-confirmation flow continues through existing `resource_reference_action`, `resource_reference_resource_id`, and `resource_reference_chapter_id`.
- Any new resource API or schema change must be a separate PR and not bundled with `chatbot.py` refactors.

## Parsing pipeline

- PDF: extract pages, headings if available, text blocks, and page locators.
- DOCX/Markdown/TXT: preserve heading/list/table structure where possible.
- Chunk by section first, then by bounded token/character windows with heading path retained.
- Record parse warnings for scanned/low-text files instead of silently inventing content.

## Retrieval strategy

- First pass: lexical/FTS matches over title, heading path, summary, keywords, and chunk text.
- Second pass: optional semantic rerank where embeddings are available.
- Return explainable `ResourceMatch` evidence with score and reason.
- Never inject unrelated resource summaries into Chatbot prompts by default.

## Privacy and logging

- Logs may record resource ids, chapter ids, scores, and short evidence excerpts.
- Avoid logging full uploaded documents or large raw chunks in AI usage logs.
- Keep source file storage outside deploy-overwritten paths.

## Test plan

- Parser tests for PDF/DOCX/Markdown/TXT fixtures.
- Retrieval tests for direct hit, ambiguous hit, low confidence, and no-resource cases.
- Chat tests proving resource prompts do not pollute unrelated ordinary chat.
- BoardEditor tests proving selected resource context is passed only after confirm/direct high-confidence resolution.

## PR breakdown

1. Resource parsing contract and fixture tests.
2. Chunk metadata and derived search index tests.
3. ResourceResolver explainable matching improvements.
4. Resource prompt/confirm flow extraction from `chatbot.py`.
5. Chatbot resource-grounded answer tests.
6. BoardEditor resource-backed generation tests.
7. Privacy/logging audit tests.
