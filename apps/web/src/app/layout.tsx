import { InterfaceLanguageProvider } from "@/contexts/interface-language-context";
import type { Metadata } from "next";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "开放课堂",
    template: "%s | 开放课堂",
  },
  description: "面向课程主页、资料管理、富文本讲义编辑和版本回溯的课程工作台。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <InterfaceLanguageProvider>{children}</InterfaceLanguageProvider>
      </body>
    </html>
  );
}
