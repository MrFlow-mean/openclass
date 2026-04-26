import type { Metadata } from "next";

import { AuthPanel } from "@/components/auth-panel";

export const metadata: Metadata = {
  title: "登录",
  description: "登录 OpenClass 账号。",
};

export default function LoginPage() {
  return <AuthPanel initialMode="login" />;
}

