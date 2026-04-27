import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { LearningHome } from "@/components/learning-home";

export const metadata: Metadata = {
  title: "交互式学习主页",
  description: "黑板 AI 的课程包、随手笔记与精品课程商城入口。",
};

export default function Home() {
  return (
    <AuthGate>
      <LearningHome />
    </AuthGate>
  );
}
