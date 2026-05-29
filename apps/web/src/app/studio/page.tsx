import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { CourseStudio } from "@/components/course-studio";

export const metadata: Metadata = {
  title: "Course Studio",
  description: "OpenClass workspace for co-editing lessons, citing resources, and AI teaching.",
};

export default function StudioPage() {
  return (
    <AuthGate>
      <CourseStudio />
    </AuthGate>
  );
}
