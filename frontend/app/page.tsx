import type { Metadata } from "next";
import { AuthenticatedHome } from "@/components/authenticated-home";

export const metadata: Metadata = {
  title: "天序 · 首页",
  description: "进入天序八字排盘、命理对话与管理工作台",
};

export default function Home() {
  return <AuthenticatedHome />;
}
