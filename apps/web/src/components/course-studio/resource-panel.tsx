import { CourseGraphPanel } from "@/components/course-studio/course-graph-panel";
import type { CoursePackage, Lesson } from "@/types";

type ResourcePanelProps = {
  activeLesson: Lesson;
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

export function ResourcePanel({
  activeLesson,
  relatedEdges,
  lessonMap,
  onOpenLesson,
}: ResourcePanelProps) {
  return (
    <div className="space-y-8">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">资料区</p>
        <div aria-label="资料区预留位" className="mt-4 min-h-40 rounded-lg border border-dashed border-gray-200 bg-white" />
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
