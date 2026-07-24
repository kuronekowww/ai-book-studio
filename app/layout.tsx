import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "知声工坊 · AI 讲书知识与文稿工作台",
  description: "从原书拆解、知识沉淀到声音稿生产的本地工作台。",
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
