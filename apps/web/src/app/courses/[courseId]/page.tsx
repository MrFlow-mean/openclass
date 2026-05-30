import type { Metadata } from "next";

import { OpenCourseDetailPage } from "@/components/open-course-detail";

type CourseDetailPageProps = {
  params: Promise<{
    courseId: string;
  }>;
};

export const metadata: Metadata = {
  title: "Open course",
  description: "Review, fork, and improve an OpenClass course.",
};

export default async function CourseDetailPage({ params }: CourseDetailPageProps) {
  const { courseId } = await params;
  return <OpenCourseDetailPage courseId={courseId} />;
}
