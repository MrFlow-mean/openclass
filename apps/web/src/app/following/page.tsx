import type { Metadata } from "next";

import { FollowingFeed } from "@/components/following-feed";

export const metadata: Metadata = {
  title: "关注动态",
  description: "查看已关注课程创作者的最近课程更新。",
};

export default function FollowingPage() {
  return <FollowingFeed />;
}
