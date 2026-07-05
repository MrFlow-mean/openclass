import clsx from "clsx";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { CoursePackage, LibraryChapter } from "@/types";

export type ResourceOutlineNode = {
  chapter: LibraryChapter;
  children: ResourceOutlineNode[];
};

const PARSER_ARTIFACT_TITLE_PATTERN = /^(?:text|image|img|table|equation|formula|figure|page)\d{3,}$/i;

function isParserArtifactTitle(title: string) {
  const compact = title.replace(/[\s_-]+/g, "").replace(/[：:]+$/g, "").toLowerCase();
  return PARSER_ARTIFACT_TITLE_PATTERN.test(compact);
}

function readableSummarySnippet(summary: string) {
  const cleaned = summary
    .replace(/来自资料标题“[^”]+”的章节摘要待进一步展开。/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned || isParserArtifactTitle(cleaned)) {
    return "";
  }
  return cleaned.length > 42 ? `${cleaned.slice(0, 42)}...` : cleaned;
}

function chapterDisplayTitle(chapter: LibraryChapter) {
  const title = chapter.title.trim();
  if (title && !isParserArtifactTitle(title)) {
    return title;
  }
  const pathTitle = [...chapter.path].reverse().find((part) => part.trim() && !isParserArtifactTitle(part));
  if (pathTitle) {
    return pathTitle.trim();
  }
  const summaryTitle = readableSummarySnippet(chapter.summary);
  if (summaryTitle) {
    return summaryTitle;
  }
  return `资料片段 ${chapter.order_index + 1}`;
}

export function buildResourceOutlineTree(outline: LibraryChapter[]) {
  const sorted = [...outline].sort((first, second) => first.order_index - second.order_index);
  const nodesById = new Map<string, ResourceOutlineNode>();
  const roots: ResourceOutlineNode[] = [];
  const stack: ResourceOutlineNode[] = [];

  sorted.forEach((chapter) => {
    const node = { chapter, children: [] };
    nodesById.set(chapter.id, node);

    const parent = chapter.parent_id ? nodesById.get(chapter.parent_id) : null;
    if (parent) {
      parent.children.push(node);
      return;
    }

    while (stack.length && stack[stack.length - 1].chapter.level >= chapter.level) {
      stack.pop();
    }
    const fallbackParent = stack[stack.length - 1];
    if (fallbackParent) {
      fallbackParent.children.push(node);
    } else {
      roots.push(node);
    }
    stack.push(node);
  });

  return roots;
}

function chapterLocationLabel(chapter: LibraryChapter) {
  if (chapter.page_range) {
    return `页 ${chapter.page_range}`;
  }
  if (chapter.page_start) {
    return `页 ${chapter.page_start}`;
  }
  return "";
}

export function ResourceOutlineTree({
  resource,
  nodes,
  expandedNodeIds,
  selectedChapterId,
  onToggleNode,
  onSelectNode,
  level = 0,
}: {
  resource: CoursePackage["resources"][number];
  nodes: ResourceOutlineNode[];
  expandedNodeIds: Set<string>;
  selectedChapterId: string | null;
  onToggleNode: (resourceId: string, chapterId: string) => void;
  onSelectNode: (resource: CoursePackage["resources"][number], chapter: LibraryChapter) => void | Promise<void>;
  level?: number;
}) {
  return (
    <div className={clsx("space-y-1", level > 0 && "ml-3 border-l border-gray-100 pl-2")}>
      {nodes.map((node) => {
        const hasChildren = node.children.length > 0;
        const isExpanded = expandedNodeIds.has(node.chapter.id);
        const isSelected = selectedChapterId === node.chapter.id;
        const location = chapterLocationLabel(node.chapter);
        const displayTitle = chapterDisplayTitle(node.chapter);

        return (
          <div key={node.chapter.id}>
            <div
              className={clsx(
                "group flex items-center gap-2 rounded-md border px-2 py-1.5 transition",
                isSelected ? "border-emerald-200 bg-emerald-50" : "border-transparent bg-gray-50/60 hover:bg-gray-50"
              )}
            >
              <div className="min-w-0 flex-1">
                <p className={clsx("truncate text-xs font-medium", isSelected ? "text-emerald-950" : "text-gray-800")}>
                  {displayTitle}
                </p>
                {location ? <p className="mt-0.5 text-[10px] text-gray-400">{location}</p> : null}
              </div>
              <button
                type="button"
                onClick={() => void onSelectNode(resource, node.chapter)}
                className="inline-flex h-6 items-center rounded-md border border-gray-200 bg-white px-2 text-[11px] font-semibold text-gray-700 shadow-sm transition hover:border-emerald-300 hover:text-emerald-700"
              >
                选择
              </button>
              <button
                type="button"
                onClick={() => hasChildren && onToggleNode(resource.id, node.chapter.id)}
                disabled={!hasChildren}
                className="inline-flex h-6 items-center gap-1 rounded-md border border-gray-200 bg-white px-2 text-[11px] font-semibold text-gray-700 shadow-sm transition hover:border-gray-300 disabled:cursor-not-allowed disabled:opacity-40"
                aria-expanded={hasChildren ? isExpanded : undefined}
              >
                {isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                展开
              </button>
            </div>
            {hasChildren && isExpanded ? (
              <ResourceOutlineTree
                resource={resource}
                nodes={node.children}
                expandedNodeIds={expandedNodeIds}
                selectedChapterId={selectedChapterId}
                onToggleNode={onToggleNode}
                onSelectNode={onSelectNode}
                level={level + 1}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
