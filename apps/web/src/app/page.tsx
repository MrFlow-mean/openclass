import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { LearningHome } from "@/components/learning-home";

export const metadata: Metadata = {
  title: "Interactive Learning Home",
  description: "OpenClass course packages, quick notes, and open course discovery.",
};

export default function Home() {
  return (
    <AuthGate>
      <LearningHome />
    </AuthGate>
  );
}
