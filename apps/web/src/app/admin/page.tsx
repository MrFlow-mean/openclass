import type { Metadata } from "next";

import { AdminDashboard } from "@/components/admin-dashboard";
import { AuthGate } from "@/components/auth-gate";

export const metadata: Metadata = {
  title: "管理员后台",
  description: "开放课堂用户与课程管理后台。",
};

export default function AdminPage() {
  return (
    <AuthGate adminOnly>
      <AdminDashboard />
    </AuthGate>
  );
}
