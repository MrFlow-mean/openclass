import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { FollowingFeed } from "@/components/following-feed";

export const metadata: Metadata = {
  title: "Following",
  description: "Course commits, resource updates, and studio activity from your feed.",
};

export default function FollowingPage() {
  return (
    <AuthGate>
      <FollowingFeed />
    </AuthGate>
  );
}
