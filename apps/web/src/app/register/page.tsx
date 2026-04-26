import type { Metadata } from "next";

import { AuthPanel } from "@/components/auth-panel";

export const metadata: Metadata = {
  title: "注册",
  description: "注册 OpenClass 账号。",
};

export default function RegisterPage() {
  return <AuthPanel initialMode="register" />;
}

