"use client";

import clsx from "clsx";
import { FileText, ImagePlus, LoaderCircle, Trash2 } from "lucide-react";

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
                        <p className="mt-1 text-[11px] text-gray-500">
                          {resource.extracted_text_available
                            ? r.indexed(resource.outline.length)
                            : r.entryOnly}
                        </p>
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
                          <p className="font-semibold text-gray-800">{chapter.title}</p>
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
