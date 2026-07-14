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
    source_excerpt: chapter.excerpt,
    source_page_range: pageRange,
    source_locator: chapter.source_locator,
    source_page_start: chapter.page_start,
    source_page_end: chapter.page_end,
  };
}

export function sourceChapterLabel(chapter: SourceChapter) {
  const title = chapter.title.trim();
  if (
    !chapter.number ||
    title.startsWith(chapter.number) ||
    /^第\s*[0-9一二三四五六七八九十百零〇两]+\s*[章节篇部]/.test(title)
  ) {
    return title;
  }
  return `${chapter.number} ${title}`.trim();
}

function sourceChapterPageRange(chapter: SourceChapter) {
  if (chapter.page_start == null) {
    return "";
  }
  const displayEnd = Math.max(chapter.page_start, (chapter.page_end ?? chapter.page_start + 1) - 1);
  if (displayEnd === chapter.page_start) {
    return `p. ${chapter.page_start}`;
  }
  return `pp. ${chapter.page_start}-${displayEnd}`;
}
