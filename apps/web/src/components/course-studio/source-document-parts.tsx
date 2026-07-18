import clsx from "clsx";

import type { SourceDocumentPart, SourceDocumentPartKind } from "@/types";

const PART_LABELS: Record<SourceDocumentPartKind, string> = {
  front_cover: "封面",
  half_title: "书名页",
  title_page: "扉页",
  copyright: "版权页",
  dedication: "题献",
  foreword: "前言",
  preface: "序言",
  introduction: "导言",
  acknowledgements: "致谢",
  table_of_contents: "目录",
  list_of_figures: "插图目录",
  list_of_tables: "表格目录",
  body: "正文",
  epilogue: "尾声",
  afterword: "后记",
  appendix: "附录",
  notes: "注释",
  glossary: "术语表",
  bibliography: "参考文献",
  index: "索引",
  colophon: "出版说明",
  back_cover: "后封面",
  unknown: "未确定",
};

export function SourceDocumentParts({ parts }: { parts: SourceDocumentPart[] }) {
  if (!parts.length) {
    return null;
  }
  return (
    <section className="mb-3 rounded-md border border-blue-100 bg-white/80 p-2" aria-label="全书篇幅结构">
      <p className="mb-2 text-[11px] font-semibold text-gray-700">全书结构</p>
      <div className="flex flex-wrap gap-1.5">
        {parts.map((part) => (
          <span
            key={part.id}
            className={clsx(
              "rounded-full border px-2 py-1 text-[10px] leading-none",
              part.anchor_status === "verified"
                ? "border-blue-100 bg-blue-50 text-blue-700"
                : "border-gray-200 bg-gray-50 text-gray-500"
            )}
            title={`${part.title || PART_LABELS[part.kind]} · ${partPageRange(part)}`}
          >
            {part.title || PART_LABELS[part.kind]}
            {part.page_start != null ? ` · ${partPageRange(part)}` : ""}
          </span>
        ))}
      </div>
    </section>
  );
}

function partPageRange(part: SourceDocumentPart) {
  if (part.page_start == null) {
    return "页码待确认";
  }
  const lastPage = Math.max(part.page_start, (part.page_end ?? part.page_start + 1) - 1);
  return lastPage === part.page_start ? `p. ${part.page_start}` : `pp. ${part.page_start}-${lastPage}`;
}
