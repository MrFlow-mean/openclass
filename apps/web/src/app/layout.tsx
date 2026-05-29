import { InterfaceLanguageProvider } from "@/contexts/interface-language-context";
import type { Metadata } from "next";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "OpenClass",
    template: "%s | OpenClass",
  },
  description: "An AI course workspace for course homes, resources, rich lesson notes, and version history.",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/icon.png", type: "image/png", sizes: "512x512" },
    ],
    apple: [{ url: "/apple-icon.png", type: "image/png", sizes: "180x180" }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <InterfaceLanguageProvider>{children}</InterfaceLanguageProvider>
      </body>
    </html>
  );
}
