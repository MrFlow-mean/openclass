import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { FollowingFeed } from "@/components/following-feed";

export const metadata: Metadata = {
  title: "学习动态",
  description: "查看与主页 Feed 同步的课程提交、资料收录和工作台更新。",
};

export default function FollowingPage() {
  return (
    <AuthGate>
      <FollowingFeed />
    </AuthGate>
  );
}
