import type { Metadata } from "next";

import { AdminDashboard } from "@/components/admin-dashboard";
import { AuthGate } from "@/components/auth-gate";

export const metadata: Metadata = {
  title: "Admin",
  description: "OpenClass user and course administration.",
};

export default function AdminPage() {
  return (
    <AuthGate adminOnly>
      <AdminDashboard />
    </AuthGate>
  );
}
