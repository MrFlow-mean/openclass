import type { SourceChapter, SourceIngestionRecord } from "@/types";

export function formatSourceChapterChatReference(source: SourceIngestionRecord, chapter: SourceChapter) {
  const chapterLabel = sourceChapterLabel(chapter);
  const path = chapter.path.length ? chapter.path.join(" > ") : chapterLabel;
  const pageRange = sourceChapterPageRange(chapter);
  const locatorParts = [chapterLabel ? `章节「${chapterLabel}」` : "已选章节", pageRange].filter(Boolean);

  return [
    `请结合已上传资料《${source.title}》中的${locatorParts.join(" · ")}回答。`,
    `资料章节引用：source_chapter_id=${chapter.id}`,
    path ? `章节路径：${path}` : "",
    "",
  ]
    .filter((line) => line.length > 0)
    .join("\n");
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
