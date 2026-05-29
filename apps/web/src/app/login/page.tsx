import type { Metadata } from "next";

import { AuthPanel } from "@/components/auth-panel";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to OpenClass.",
};

export default function LoginPage() {
  return <AuthPanel initialMode="login" />;
}
