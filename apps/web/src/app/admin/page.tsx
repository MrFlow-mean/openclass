import type { Metadata } from "next";

import { AdminDashboard } from "@/components/admin-dashboard";

export const metadata: Metadata = {
  title: "管理员后台",
  description: "OpenClass 用户与课程管理后台。",
};

export default function AdminPage() {
  return <AdminDashboard />;
}
