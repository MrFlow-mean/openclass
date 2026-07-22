import type { Metadata } from "next";

import { AuthGate } from "@/components/auth-gate";
import { WalletHome } from "@/components/wallet-home";

export const metadata: Metadata = {
  title: "积分与充值",
  description: "OpenClass 模型调用积分与充值入口。",
};

export default function WalletPage() {
  return (
    <AuthGate>
      <WalletHome />
    </AuthGate>
  );
}
