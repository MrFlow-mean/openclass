import type { Metadata } from "next";

import { TechnicalDocsPage } from "@/components/technical-docs-page";

export const metadata: Metadata = {
  title: "技术文档",
  description: "开放课堂的工程结构、AI 协作边界、本地运行和验证命令说明。",
};

export default function TechDocsPage() {
  return <TechnicalDocsPage />;
}
