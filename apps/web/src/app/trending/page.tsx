import type { Metadata } from "next";

import { TrendingCourses } from "@/components/trending-courses";

export const metadata: Metadata = {
  title: "Trending",
  description: "Explore popular open course projects in OpenClass.",
};

export default function TrendingPage() {
  return <TrendingCourses />;
}
