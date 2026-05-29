import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { ProfileHome } from "@/components/profile-home";

export const metadata: Metadata = {
  title: "Profile",
  description: "OpenClass profile, repositories, and starred courses.",
};

type ProfilePageProps = {
  searchParams?: Promise<{
    tab?: string | string[];
  }>;
};

export default async function ProfilePage({ searchParams }: ProfilePageProps) {
  const params = await searchParams;
  const tabParam = Array.isArray(params?.tab) ? params?.tab[0] : params?.tab;

  return (
    <AuthGate>
      <ProfileHome
        initialTab={tabParam === "repositories" ? "repositories" : tabParam === "stars" ? "stars" : "settings"}
      />
    </AuthGate>
  );
}
