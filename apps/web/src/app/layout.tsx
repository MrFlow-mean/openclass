import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 黑板课程工作台",
  description: "面向课程共编、版本回溯和 AI 讲解的板书系统",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
