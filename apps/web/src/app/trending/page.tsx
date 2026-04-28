import type { Metadata } from "next";

import { TrendingCourses } from "@/components/trending-courses";

export const metadata: Metadata = {
  title: "热门项目",
  description: "像 GitHub Explore 一样浏览 OpenClass 的热门开源课程项目。",
};

export default function TrendingPage() {
  return <TrendingCourses />;
}
