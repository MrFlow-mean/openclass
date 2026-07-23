import type { SourceSortOption } from "@/components/course-studio/source-batch-controls";
import type { SourceCatalogView, SourceIngestionRecord } from "@/types";

const SOURCE_TITLE_COLLATOR = new Intl.Collator("zh-CN", {
  numeric: true,
  sensitivity: "base",
});
const ACTIVE_SOURCE_STATUSES = new Set<SourceIngestionRecord["status"]>([
  "queued",
  "fetching",
  "parsing",
  "indexing",
]);
const ACTIVE_STRUCTURE_STATUSES = new Set<SourceIngestionRecord["structure_status"]>([
  "pending",
  "building",
]);

const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
  "www.youtu.be",
]);

export function isYouTubeSourceUrl(raw: string) {
  try {
    return YOUTUBE_HOSTS.has(new URL(raw.trim()).hostname.toLowerCase());
  } catch {
    return false;
  }
}

export function sortSources(sources: SourceIngestionRecord[], sortOption: SourceSortOption) {
  return sources
    .map((source, index) => ({ source, index }))
    .sort((left, right) => {
      if (sortOption === "name_asc" || sortOption === "name_desc") {
        const titleOrder = SOURCE_TITLE_COLLATOR.compare(
          left.source.title || left.source.file_name,
          right.source.title || right.source.file_name
        );
        if (titleOrder !== 0) return sortOption === "name_asc" ? titleOrder : -titleOrder;
      } else {
        const leftCreatedAt = Date.parse(left.source.created_at) || 0;
        const rightCreatedAt = Date.parse(right.source.created_at) || 0;
        const createdAtOrder = leftCreatedAt - rightCreatedAt;
        if (createdAtOrder !== 0) return sortOption === "uploaded_asc" ? createdAtOrder : -createdAtOrder;
      }
      return left.index - right.index;
    })
    .map(({ source }) => source);
}

export function mergeSourceWithCatalog(
  source: SourceIngestionRecord,
  catalog: SourceCatalogView
): SourceIngestionRecord {
  return {
    ...source,
    ...catalog.source,
    structure_status: catalog.status,
    structure_strategy: catalog.strategy,
    structure_has_verified_toc: catalog.has_verified_toc,
    structure_quality: catalog.quality,
    structure_error: catalog.error,
    structure_updated_at: catalog.catalog_updated_at,
  };
}

export function sourceNeedsRefresh(source: SourceIngestionRecord) {
  if (ACTIVE_SOURCE_STATUSES.has(source.status)) return true;
  return source.status === "ready" && ACTIVE_STRUCTURE_STATUSES.has(source.structure_status);
}

export function metadataString(source: SourceIngestionRecord, key: string) {
  const value = source.metadata?.[key];
  return typeof value === "string" ? value : "";
}
