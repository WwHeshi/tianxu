"use client";

import {
  ArrowRight,
  CalendarDays,
  FileText,
  FlaskConical,
  LibraryBig,
  MessageCircle,
  Network,
  Settings,
  ShieldCheck,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import {
  getKnowledgeGraphStatus,
  getModelSettings,
  listKnowledgeDocuments,
} from "@/lib/api";
import type {
  CurrentUser,
  KnowledgeGraphStatus,
  ModelSettings,
} from "@/lib/types";

interface HomeDashboardProps {
  currentUser: CurrentUser;
  onLogout: () => void;
}

interface AdminOverview {
  model: ModelSettings | null;
  documentCount: number | null;
  graph: KnowledgeGraphStatus | null;
}

export function HomeDashboard({ currentUser, onLogout }: HomeDashboardProps) {
  const [adminOverview, setAdminOverview] = useState<AdminOverview>({
    model: null,
    documentCount: null,
    graph: null,
  });
  const [isAdminOverviewLoading, setIsAdminOverviewLoading] = useState(
    currentUser.role === "admin",
  );

  useEffect(() => {
    if (currentUser.role !== "admin") return;
    let active = true;
    void Promise.allSettled([
      getModelSettings(),
      listKnowledgeDocuments(),
      getKnowledgeGraphStatus(),
    ]).then(([modelResult, documentsResult, graphResult]) => {
      if (!active) return;
      setAdminOverview({
        model: modelResult.status === "fulfilled" ? modelResult.value : null,
        documentCount:
          documentsResult.status === "fulfilled" ? documentsResult.value.total : null,
        graph: graphResult.status === "fulfilled" ? graphResult.value : null,
      });
      setIsAdminOverviewLoading(false);
    });
    return () => {
      active = false;
    };
  }, [currentUser.role]);

  return (
    <main className="home-shell">
      <AppHeader
        currentUser={currentUser}
        section="首页"
        onLogout={onLogout}
        showNavigation={false}
      />

      <div className="home-content">
        <section className="home-hero">
          <div className="home-hero-copy">
            <p className="eyebrow">WELCOME TO TIANXU</p>
            <h1>欢迎回来，{currentUser.display_name}</h1>
            <p>
              从准确排盘开始，结合原始典籍、规则图谱与 Agent 推理，
              获得可追溯、可继续追问的命理解读。
            </p>
          </div>
          <aside className="home-method-card" aria-label="天序分析方法">
            <span className="home-method-icon" aria-hidden="true">
              <ShieldCheck size={22} />
            </span>
            <p>确定性计算 · Agent 解读</p>
            <h2>定天时之序，观人生之势</h2>
            <ol>
              <li><span>01</span>校经纬，正天时</li>
              <li><span>02</span>循节律，定四柱</li>
              <li><span>03</span>引典章，观万象</li>
            </ol>
          </aside>
        </section>

        <section className="home-section" aria-labelledby="home-core-title">
          <div className="home-section-heading">
            <div>
              <p className="eyebrow">CORE WORKSPACE</p>
              <h2 id="home-core-title">从这里开始</h2>
            </div>
            <p>两个核心入口，分别用于完整排盘和连续咨询。</p>
          </div>
          <div className="home-core-grid">
            <Link className="home-core-card is-chart" href="/chart">
              <span className="home-card-icon" aria-hidden="true"><CalendarDays size={23} /></span>
              <div>
                <small>DETERMINISTIC CHART</small>
                <h3>八字排盘与分析</h3>
                <p>输入出生资料，查看四柱、大运、流年、流月并生成 AI 分析报告。</p>
              </div>
              <span className="home-card-action">进入排盘 <ArrowRight size={15} /></span>
            </Link>
            <Link className="home-core-card is-chat" href="/chat">
              <span className="home-card-icon" aria-hidden="true"><MessageCircle size={23} /></span>
              <div>
                <small>CONVERSATIONAL AGENT</small>
                <h3>多轮命理对话</h3>
                <p>咨询一般命理知识，或在排盘后绑定命盘继续追问。</p>
              </div>
              <span className="home-card-action">开始对话 <ArrowRight size={15} /></span>
            </Link>
          </div>
        </section>

        {currentUser.role === "admin" && (
          <div className="home-lower-grid">
            <section className="home-panel home-admin-panel" aria-labelledby="home-admin-title">
              <header>
                <div>
                  <p className="eyebrow">ADMIN CONSOLE</p>
                  <h2 id="home-admin-title">管理工作台</h2>
                </div>
                <span><ShieldCheck size={14} />管理员可见</span>
              </header>
              <div className="home-admin-links">
                <Link href="/admin/knowledge">
                  <span><LibraryBig size={18} /></span>
                  <div><strong>知识库</strong><small>资料上传、检索与原文阅读</small></div>
                  <ArrowRight size={15} />
                </Link>
                <Link href="/admin/graph">
                  <span><Network size={18} /></span>
                  <div><strong>规则图谱</strong><small>关系浏览与规则整理任务</small></div>
                  <ArrowRight size={15} />
                </Link>
                <Link href="/admin/evaluations">
                  <span><FlaskConical size={18} /></span>
                  <div><strong>评测中心</strong><small>运行并查看模型评测结果</small></div>
                  <ArrowRight size={15} />
                </Link>
                <Link href="/admin/users">
                  <span><Users size={18} /></span>
                  <div><strong>用户管理</strong><small>账户、权限与登录会话</small></div>
                  <ArrowRight size={15} />
                </Link>
              </div>
            </section>
            <section className="home-panel home-overview-panel" aria-labelledby="home-overview-title">
              <header>
                <div>
                  <p className="eyebrow">SYSTEM OVERVIEW</p>
                  <h2 id="home-overview-title">系统概况</h2>
                </div>
                <span><ShieldCheck size={14} />管理员可见</span>
              </header>
              <div className="home-system-status" aria-busy={isAdminOverviewLoading}>
                <Link href="/chart?settings=1">
                  <span><Settings size={16} aria-hidden="true" /></span>
                  <div><strong>模型 API</strong><small>Agent 调用配置</small></div>
                  <em>
                    {isAdminOverviewLoading
                      ? "读取中"
                      : adminOverview.model === null
                        ? "暂不可用"
                        : adminOverview.model.configured
                          ? adminOverview.model.model ?? "已配置"
                          : "待配置"}
                  </em>
                </Link>
                <Link href="/admin/knowledge">
                  <span><FileText size={16} aria-hidden="true" /></span>
                  <div><strong>知识资料</strong><small>TXT 原始资料</small></div>
                  <em>
                    {isAdminOverviewLoading
                      ? "读取中"
                      : adminOverview.documentCount === null
                        ? "暂不可用"
                        : adminOverview.documentCount + " 份"}
                  </em>
                </Link>
                <Link href="/admin/graph">
                  <span><Network size={16} aria-hidden="true" /></span>
                  <div><strong>规则图谱</strong><small>Neo4j 实时状态</small></div>
                  <em>
                    {isAdminOverviewLoading
                      ? "读取中"
                      : adminOverview.graph === null
                        ? "暂不可用"
                        : adminOverview.graph.connected
                          ? adminOverview.graph.node_count + " 个节点"
                          : "未连接"}
                  </em>
                </Link>
              </div>
            </section>
          </div>
        )}

      </div>
    </main>
  );
}
