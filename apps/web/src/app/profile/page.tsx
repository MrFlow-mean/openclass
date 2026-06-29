import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { ProfileHome } from "@/components/profile-home";

export const metadata: Metadata = {
  title: "个人主页",
  description: "开放课堂的个人项目与收藏项目主页。",
};

export default async function ProfilePage() {
  return (
    <AuthGate>
      <ProfileHome />
    </AuthGate>
  );
}
