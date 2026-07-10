import type { SelectionRef, SourceChapter, SourceIngestionRecord } from "@/types";

export function createSourceChapterSelection(source: SourceIngestionRecord, chapter: SourceChapter): SelectionRef {
  const chapterLabel = sourceChapterLabel(chapter);
  const path = chapter.path.length ? chapter.path.join(" > ") : chapterLabel;
  const pageRange = sourceChapterPageRange(chapter);
  return {
    kind: "source",
    excerpt: [`《${source.title}》`, path, pageRange].filter(Boolean).join(" · "),
    heading_path: chapter.path,
    source_ingestion_id: source.id,
    source_title: source.title,
    source_uri: source.source_uri,
    source_chapter_id: chapter.id,
    source_chapter_number: chapter.number,
    source_chapter_title: chapter.title,
    source_page_range: pageRange,
  };
}

function sourceChapterLabel(chapter: SourceChapter) {
  if (!chapter.number || chapter.title.trim().startsWith(chapter.number)) {
    return chapter.title.trim();
  }
  return `${chapter.number} ${chapter.title}`.trim();
}

function sourceChapterPageRange(chapter: SourceChapter) {
  if (chapter.page_start == null) {
    return "";
  }
  if (chapter.page_end == null || chapter.page_end === chapter.page_start) {
    return `p. ${chapter.page_start}`;
  }
  return `pp. ${chapter.page_start}-${chapter.page_end}`;
}
