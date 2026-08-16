import type { Metadata } from "next";

import { AuthenticatedWorkbench } from "@/components/authenticated-workbench";

export const metadata: Metadata = {
  title: "八字排盘 · 天序",
  description: "基于固定历法规则生成可追溯的四柱命盘",
};

export default function ChartPage() {
  return <AuthenticatedWorkbench />;
}
