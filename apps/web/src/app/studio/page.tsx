import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { CourseStudio } from "@/components/course-studio";

export const metadata: Metadata = {
  title: "课程工作台",
  description: "进入黑板 AI 的课程共编、资料引用与 AI 讲解工作台。",
};

export default function StudioPage() {
  return (
    <AuthGate>
      <CourseStudio />
    </AuthGate>
  );
}
