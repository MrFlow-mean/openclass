"use client";

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleDashed, FileText, ImagePlus, LoaderCircle, Trash2 } from "lucide-react";

import { CourseGraphPanel } from "@/components/course-studio/course-graph-panel";
import { ResourceUploadDropzone } from "@/components/resource-upload-dropzone";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type { CoursePackage, Lesson } from "@/types";

type ResourcePanelProps = {
  activeLesson: Lesson;
  busyAction: string | null;
  resources: CoursePackage["resources"];
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onUploadResource: (file: File | null) => void | Promise<void>;
  onDeleteResource: (resourceId: string, resourceName: string) => void | Promise<void>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

export function ResourcePanel({
  activeLesson,
  busyAction,
  resources,
  relatedEdges,
  lessonMap,
  onUploadResource,
  onDeleteResource,
  onOpenLesson,
}: ResourcePanelProps) {
  const { texts: txt } = useInterfaceLanguage();
  const r = txt.studio.resourcePanel;
  return (
    <div className="space-y-8">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{r.title}</p>
        <ResourceUploadDropzone
          disabled={Boolean(busyAction)}
          uploading={busyAction === "upload"}
          onUpload={(file) => void onUploadResource(file)}
        />
        <div className="mt-4 space-y-3">
          {resources.length
            ? resources.map((resource) => {
                const isDeletingResource = busyAction === `delete-resource:${resource.id}`;
                const status = resourceStatus(resource, r);
                return (
                  <div
                    key={resource.id}
                    className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-gray-300"
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                        {resource.resource_type === "image" || resource.mime_type.startsWith("image/") ? (
                          <ImagePlus className="h-4 w-4" />
                        ) : (
                          <FileText className="h-4 w-4" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-bold text-gray-900">{resource.name}</p>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold", status.className)}>
                            {status.icon}
                            {status.label}
                          </span>
                          {resource.index_status === "ready" ? (
                            <span className="text-[11px] text-gray-500">{r.indexedBlocks(resource.page_count, resource.indexed_block_count)}</span>
                          ) : (
                            <span className="text-[11px] text-gray-500">{resource.index_message || r.chaptersCount(resource.outline.length)}</span>
                          )}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => void onDeleteResource(resource.id, resource.name)}
                        disabled={Boolean(busyAction)}
                        title={r.deleteTitle(resource.name)}
                        aria-label={r.deleteAria(resource.name)}
                        className={clsx(
                          "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600",
                          busyAction && "cursor-not-allowed opacity-50 hover:bg-transparent hover:text-gray-400"
                        )}
                      >
                        {isDeletingResource ? (
                          <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                    <div className="mt-3 space-y-2">
                      {resource.outline.slice(0, 3).map((chapter) => (
                        <div key={chapter.id} className="rounded-lg bg-gray-50 px-3 py-2 text-[11px] text-gray-600">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <p className="min-w-0 font-semibold text-gray-800">{chapter.title}</p>
                            {chapter.page_range ? (
                              <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10px] font-semibold text-gray-500">
                                {r.pageRange(chapter.page_range)}
                              </span>
                            ) : null}
                          </div>
                          <p className="mt-1 leading-6">{chapter.summary}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })
            : null}
        </div>
      </div>

      <CourseGraphPanel
        activeLesson={activeLesson}
        relatedEdges={relatedEdges}
        lessonMap={lessonMap}
        onOpenLesson={onOpenLesson}
      />
    </div>
  );
}

function resourceStatus(resource: CoursePackage["resources"][number], labels: ReturnType<typeof useInterfaceLanguage>["texts"]["studio"]["resourcePanel"]) {
  if (resource.index_status === "queued") {
    return {
      label: labels.queued,
      className: "bg-blue-100 text-blue-800",
      icon: <CircleDashed className="h-3 w-3" />,
    };
  }
  if (resource.index_status === "processing") {
    return {
      label: labels.processing,
      className: "bg-blue-100 text-blue-800",
      icon: <LoaderCircle className="h-3 w-3 animate-spin" />,
    };
  }
  if (resource.index_status === "failed") {
    return {
      label: labels.failed,
      className: "bg-red-100 text-red-800",
      icon: <AlertTriangle className="h-3 w-3" />,
    };
  }
  if (resource.index_status === "no_text") {
    return {
      label: labels.missingText,
      className: "bg-amber-100 text-amber-900",
      icon: <AlertTriangle className="h-3 w-3" />,
    };
  }
  if (resource.extracted_text_available) {
    return {
      label: labels.indexedText,
      className: "bg-emerald-100 text-emerald-800",
      icon: <CheckCircle2 className="h-3 w-3" />,
    };
  }
  if (resource.outline.length) {
    return {
      label: labels.metadataOnly,
      className: "bg-amber-100 text-amber-900",
      icon: <CircleDashed className="h-3 w-3" />,
    };
  }
  return {
    label: labels.missingText,
    className: "bg-red-100 text-red-800",
    icon: <AlertTriangle className="h-3 w-3" />,
  };
}
