import type { Metadata } from "next";

import { ProfileHome } from "@/components/profile-home";

export const metadata: Metadata = {
  title: "个人主页",
  description: "黑板 AI 的个人项目与收藏项目主页。",
};

export default function ProfilePage() {
  return <ProfileHome />;
}
