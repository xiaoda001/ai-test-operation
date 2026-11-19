import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 内容运营 Agent",
  description: "AI 内容运营 Agent 平台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
