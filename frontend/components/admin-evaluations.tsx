"use client";

import {
  AlertCircle,
  ArrowLeft,
  Ban,
  CheckCircle2,
  Clock3,
  Download,
  FlaskConical,
  History,
  LoaderCircle,
  LogOut,
  Play,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Workflow,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AgentDebugModal } from "@/components/agent-debug-modal";
import {
  ApiError,
  cancelEvaluationRun,
  deleteEvaluationRun,
  downloadEvaluationExport,
  getCurrentUser,
  getEvaluationItemTrace,
  getEvaluationItems,
  getEvaluationOverview,
  getEvaluationRun,
  listEvaluationRuns,
  logout,
  startEvaluationRun,
} from "@/lib/api";
import type {
  CurrentUser,
  EvaluationItem,
  EvaluationItemTrace,
  EvaluationOverview,
  EvaluationRunDetail,
  EvaluationRunStatus,
  EvaluationRunSummary,
  EvaluationScope,
} from "@/lib/types";

const ACTIVE_STATUSES: EvaluationRunStatus[] = ["queued", "running", "cancel_requested"];
const STATUS_LABELS: Record<EvaluationRunStatus, string> = {
  queued: "等待运行",
  running: "评测中",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  completed: "已完成",
  failed: "失败",
};

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function scopeLabel(run: EvaluationRunSummary): string {
  if (run.scope === "quick") return "快速测试 · 5题";
  if (run.scope === "year") return `${run.benchmark_year}年 · 40题`;
  return "完整评测 · 160题";
}

export function AdminEvaluations() {
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [overview, setOverview] = useState<EvaluationOverview | null>(null);
  const [runs, setRuns] = useState<EvaluationRunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvaluationRunDetail | null>(null);
  const [items, setItems] = useState<EvaluationItem[]>([]);
  const [scope, setScope] = useState<EvaluationScope>("quick");
  const [benchmarkYear, setBenchmarkYear] = useState<2022 | 2023 | 2024 | 2025>(2022);
  const [maxConcurrency, setMaxConcurrency] = useState(2);
  const [resultFilter, setResultFilter] = useState<"all" | "correct" | "incorrect" | "error">("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [activeTrace, setActiveTrace] = useState<EvaluationItemTrace | null>(null);
  const [traceLoadingItemId, setTraceLoadingItemId] = useState<number | null>(null);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);

  const selectedActive = selectedRun ? ACTIVE_STATUSES.includes(selectedRun.status) : false;
  const requestCount = scope === "quick" ? 5 : scope === "year" ? 40 : 160;
  const canStart = Boolean(
    overview?.dataset.available
      && overview.model_configured
      && !overview.active_run
      && !isStarting,
  );

  const loadRun = useCallback(async (
    runId: string,
    filter: "all" | "correct" | "incorrect" | "error" = "all",
    quiet = false,
  ) => {
    if (!quiet) setError("");
    const [detail, itemList] = await Promise.all([
      getEvaluationRun(runId),
      getEvaluationItems(runId, filter === "all" ? undefined : filter),
    ]);
    setSelectedRun(detail);
    setItems(itemList.items);
    return detail;
  }, []);

  const loadPage = useCallback(async () => {
    setError("");
    try {
      const me = await getCurrentUser();
      if (me.must_change_password) {
        window.location.replace("/change-password");
        return;
      }
      if (me.role !== "admin") {
        window.location.replace("/");
        return;
      }
      const [nextOverview, runList] = await Promise.all([
        getEvaluationOverview(),
        listEvaluationRuns(),
      ]);
      setCurrentUser(me);
      setOverview(nextOverview);
      setRuns(runList.items);
      const targetId = nextOverview.active_run?.id ?? runList.items[0]?.id;
      if (targetId) {
        setResultFilter("all");
        await loadRun(targetId, "all", true);
      }
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        window.location.replace("/login");
        return;
      }
      setError(requestError instanceof Error ? requestError.message : "无法读取评测中心。仅支持管理员访问。");
    } finally {
      setIsLoading(false);
    }
  }, [loadRun]);

  useEffect(() => {
    void loadPage();
  }, [loadPage]);

  useEffect(() => {
    if (!selectedRun || !selectedActive) return;
    const timer = window.setInterval(() => {
      void loadRun(selectedRun.id, resultFilter, true)
        .then((nextRun) => {
          if (!ACTIVE_STATUSES.includes(nextRun.status)) {
            void Promise.all([getEvaluationOverview(), listEvaluationRuns()]).then(
              ([nextOverview, runList]) => {
                setOverview(nextOverview);
                setRuns(runList.items);
              },
            );
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [loadRun, resultFilter, selectedActive, selectedRun]);

  useEffect(() => {
    const runId = selectedRun?.id;
    if (!runId) return;
    void loadRun(runId, resultFilter, true).catch((requestError) => {
      setError(requestError instanceof Error ? requestError.message : "无法筛选逐题结果。");
    });
  }, [loadRun, resultFilter, selectedRun?.id]);

  async function handleStart() {
    const label = scope === "quick" ? "快速测试" : scope === "year" ? `${benchmarkYear}年评测` : "完整评测";
    if (!window.confirm(`将启动${label}并调用当前模型 ${requestCount} 次，可能产生 API 费用。确认开始吗？`)) return;
    setError("");
    setNotice("");
    setIsStarting(true);
    try {
      const run = await startEvaluationRun({
        scope,
        benchmark_year: scope === "year" ? benchmarkYear : null,
        mode: "tianxu_fortune",
        max_concurrency: maxConcurrency,
        confirmed_request_count: requestCount,
      });
      setSelectedRun(run);
      setItems([]);
      setResultFilter("all");
      setNotice(`评测已进入后台队列，共 ${requestCount} 道题。关闭页面不会中断运行。`);
      const [nextOverview, runList] = await Promise.all([
        getEvaluationOverview(),
        listEvaluationRuns(),
      ]);
      setOverview(nextOverview);
      setRuns(runList.items);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法启动评测。请检查模型和数据集状态。");
    } finally {
      setIsStarting(false);
    }
  }

  async function handleCancel() {
    if (!selectedRun || !window.confirm("确认取消这次评测吗？已完成的题目会保留。")) return;
    setError("");
    try {
      setSelectedRun(await cancelEvaluationRun(selectedRun.id));
      setNotice("已提交取消请求，正在等待当前批次结束。");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "取消评测失败。");
    }
  }

  async function handleExport(format: "json" | "csv") {
    if (!selectedRun) return;
    setError("");
    try {
      await downloadEvaluationExport(selectedRun.id, format);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "导出评测结果失败。");
    }
  }

  async function handleDeleteRun(run: EvaluationRunSummary) {
    const confirmed = window.confirm(
      `确认永久删除“${scopeLabel(run)}”吗？逐题结果和调用链路也会一并删除，此操作无法恢复。`,
    );
    if (!confirmed) return;
    setError("");
    setNotice("");
    setDeletingRunId(run.id);
    try {
      await deleteEvaluationRun(run.id);
      const [nextOverview, runList] = await Promise.all([
        getEvaluationOverview(),
        listEvaluationRuns(),
      ]);
      setOverview(nextOverview);
      setRuns(runList.items);
      if (selectedRun?.id === run.id) {
        setActiveTrace(null);
        const targetId = nextOverview.active_run?.id ?? runList.items[0]?.id;
        if (targetId) {
          setResultFilter("all");
          await loadRun(targetId, "all", true);
        } else {
          setSelectedRun(null);
          setItems([]);
        }
      }
      setNotice("历史评测已删除。");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "删除历史评测失败。");
    } finally {
      setDeletingRunId(null);
    }
  }

  async function handleOpenTrace(item: EvaluationItem) {
    if (!selectedRun) return;
    setError("");
    setTraceLoadingItemId(item.id);
    try {
      setActiveTrace(await getEvaluationItemTrace(selectedRun.id, item.id));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法读取该题的执行链路。");
    } finally {
      setTraceLoadingItemId(null);
    }
  }

  async function handleLogout() {
    await logout().catch(() => undefined);
    window.location.replace("/login");
  }

  const wrongCount = selectedRun
    ? selectedRun.completed_questions - selectedRun.correct_answers - selectedRun.error_count
    : 0;
  const datasetHash = useMemo(
    () => overview?.dataset.sha256?.slice(0, 12) ?? "—",
    [overview?.dataset.sha256],
  );

  if (isLoading) {
    return <main className="auth-state-page"><LoaderCircle className="spin" size={24} /><p>正在读取评测配置</p></main>;
  }

  return (
    <main className="admin-page evaluation-page">
      <header className="admin-topbar">
        <Link href="/" className="admin-back"><ArrowLeft size={16} />返回排盘</Link>
        <div><ShieldCheck size={17} /><span>{currentUser?.display_name}</span></div>
        <button type="button" onClick={() => void handleLogout()}><LogOut size={16} />退出</button>
      </header>

      <div className="admin-layout evaluation-layout">
        <section className="admin-heading evaluation-heading">
          <div><p className="eyebrow">AGENT EVALUATION</p><h1>评测中心</h1></div>
          <span>MingLi-Bench · 天序完整运势模式</span>
        </section>

        {error && <p className="admin-message is-error" role="alert"><AlertCircle size={16} />{error}</p>}
        {notice && <p className="admin-message is-success" role="status"><CheckCircle2 size={16} />{notice}</p>}

        <section className="evaluation-status-grid" aria-label="评测环境状态">
          <article data-ready={overview?.dataset.available || undefined}>
            <FlaskConical size={18} />
            <div><span>本地数据集</span><strong>{overview?.dataset.available ? `${overview.dataset.question_count}题 · ${overview.dataset.case_count}命例` : "不可用"}</strong><small>SHA-256 {datasetHash}</small></div>
          </article>
          <article data-ready={overview?.model_configured || undefined}>
            <Sparkles size={18} />
            <div><span>当前模型</span><strong>{overview?.model_configured ? overview.model : "尚未配置"}</strong><small>{overview?.api_protocol ?? "请先返回排盘页设置 API"}</small></div>
          </article>
          <article data-ready={!overview?.active_run || undefined}>
            <Clock3 size={18} />
            <div><span>任务队列</span><strong>{overview?.active_run ? STATUS_LABELS[overview.active_run.status] : "可以启动"}</strong><small>{overview?.active_run ? `${overview.active_run.completed_questions}/${overview.active_run.total_questions}` : "同一时间只运行一项"}</small></div>
          </article>
        </section>

        {!overview?.dataset.available && (
          <p className="admin-message is-error"><AlertCircle size={16} />{overview?.dataset.error}</p>
        )}

        <section className="evaluation-launch-card" aria-labelledby="evaluation-launch-title">
          <div className="evaluation-card-title"><Play size={18} /><div><h2 id="evaluation-launch-title">启动新评测</h2><p>每道题独立调用当前模型，答案不会进入模型提示词。</p></div></div>
          <div className="evaluation-launch-grid">
            <fieldset>
              <legend>评测范围</legend>
              <div className="evaluation-scope-options">
                {([
                  ["quick", "快速5题"],
                  ["year", "单年40题"],
                  ["all", "全部160题"],
                ] as Array<[EvaluationScope, string]>).map(([value, label]) => (
                  <label key={value} data-selected={scope === value || undefined}>
                    <input type="radio" name="evaluation-scope" checked={scope === value} onChange={() => setScope(value)} />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <label><span>比赛年份</span><select value={benchmarkYear} disabled={scope !== "year"} onChange={(event) => setBenchmarkYear(Number(event.target.value) as 2022 | 2023 | 2024 | 2025)}>{[2022, 2023, 2024, 2025].map((year) => <option key={year}>{year}</option>)}</select></label>
            <label><span>并发请求</span><select value={maxConcurrency} onChange={(event) => setMaxConcurrency(Number(event.target.value))}>{[1, 2, 3, 4].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
            <div className="evaluation-request-count"><span>预计调用</span><strong>{requestCount}</strong><small>次模型请求</small></div>
            <button type="button" disabled={!canStart} onClick={() => void handleStart()}>{isStarting ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{isStarting ? "正在创建" : "开始评测"}</button>
          </div>
          {!overview?.model_configured && <p className="evaluation-inline-hint">需要先在排盘首页由管理员配置模型 API。</p>}
        </section>

        {selectedRun && (
          <>
            <section className="evaluation-run-card" aria-labelledby="current-evaluation-title">
              <header>
                <div><span className={`evaluation-status is-${selectedRun.status}`}>{STATUS_LABELS[selectedRun.status]}</span><h2 id="current-evaluation-title">{scopeLabel(selectedRun)}</h2><small>{selectedRun.model} · {formatDate(selectedRun.created_at)}</small></div>
                {selectedActive && <button type="button" onClick={() => void handleCancel()}><Ban size={14} />取消评测</button>}
              </header>
              <div className="evaluation-progress" aria-label={`完成 ${selectedRun.completed_questions}/${selectedRun.total_questions}`}>
                <div style={{ width: `${selectedRun.progress * 100}%` }} />
              </div>
              <div className="evaluation-metrics">
                <div><span>进度</span><strong>{selectedRun.completed_questions}/{selectedRun.total_questions}</strong></div>
                <div><span>准确率</span><strong>{percent(selectedRun.accuracy)}</strong></div>
                <div><span>正确</span><strong>{selectedRun.correct_answers}</strong></div>
                <div><span>答错</span><strong>{wrongCount}</strong></div>
                <div><span>接口/格式错误</span><strong>{selectedRun.error_count}</strong></div>
                <div><span>Token</span><strong>{(selectedRun.input_tokens + selectedRun.output_tokens).toLocaleString()}</strong></div>
              </div>
              {selectedRun.failure_message && <p className="admin-message is-error"><XCircle size={15} />{selectedRun.failure_message}</p>}
            </section>

            <section className="evaluation-breakdown-grid">
              <BreakdownCard title="按年份" values={selectedRun.by_year} />
              <BreakdownCard title="按分类" values={selectedRun.by_category} />
            </section>

            <section className="admin-table-card evaluation-items-card">
              <div className="admin-table-header"><strong>逐题结果</strong><div className="evaluation-table-actions"><select value={resultFilter} onChange={(event) => setResultFilter(event.target.value as typeof resultFilter)}><option value="all">全部</option><option value="correct">只看正确</option><option value="incorrect">只看答错</option><option value="error">只看错误</option></select><button type="button" onClick={() => void handleExport("json")}><Download size={14} />JSON</button><button type="button" onClick={() => void handleExport("csv")}><Download size={14} />CSV</button><button type="button" onClick={() => void loadRun(selectedRun.id, resultFilter)}><RefreshCcw size={14} />刷新</button></div></div>
              <div className="admin-table-wrap">
                <table><thead><tr><th>题目</th><th>年份 / 分类</th><th>模型答案</th><th>正确答案</th><th>结果</th><th>耗时</th><th>调用链路</th></tr></thead>
                  <tbody>{items.map((item) => (
                    <EvaluationItemRow
                      item={item}
                      key={item.id}
                      loadingTrace={traceLoadingItemId === item.id}
                      onOpenTrace={() => void handleOpenTrace(item)}
                    />
                  ))}</tbody>
                </table>
                {items.length === 0 && <p className="evaluation-empty">尚无符合条件的逐题结果。</p>}
              </div>
            </section>
          </>
        )}

        <section className="admin-table-card evaluation-history-card">
          <div className="admin-table-header"><strong><History size={14} />历史评测</strong><button type="button" onClick={() => void loadPage()}><RefreshCcw size={14} />刷新</button></div>
          <div className="evaluation-history-list">
            {runs.map((run) => (
              <div className="evaluation-history-item" key={run.id} data-selected={selectedRun?.id === run.id || undefined}>
                <button className="evaluation-history-select" type="button" onClick={() => void loadRun(run.id, resultFilter)}>
                  <span className={`evaluation-status is-${run.status}`}>{STATUS_LABELS[run.status]}</span><strong>{scopeLabel(run)}</strong><small>{percent(run.accuracy)} · {run.completed_questions}/{run.total_questions} · {formatDate(run.created_at)}</small>
                </button>
                <button
                  className="evaluation-history-delete"
                  type="button"
                  title={ACTIVE_STATUSES.includes(run.status) ? "运行中的评测不能删除" : "删除历史评测"}
                  aria-label={`删除${scopeLabel(run)}`}
                  disabled={ACTIVE_STATUSES.includes(run.status) || deletingRunId === run.id}
                  onClick={() => void handleDeleteRun(run)}
                >
                  {deletingRunId === run.id ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}
                </button>
              </div>
            ))}
            {runs.length === 0 && <p className="evaluation-empty">还没有评测记录。</p>}
          </div>
        </section>
      </div>
      {activeTrace && (
        <EvaluationTraceModal trace={activeTrace} onClose={() => setActiveTrace(null)} />
      )}
    </main>
  );
}

function BreakdownCard({ title, values }: { title: string; values: EvaluationRunDetail["by_year"] }) {
  return <section className="evaluation-breakdown-card"><h3>{title}</h3><div>{values.map((value) => <article key={value.key}><span>{value.key}</span><strong>{percent(value.accuracy)}</strong><small>{value.correct}/{value.completed}{value.errors ? ` · 错误${value.errors}` : ""}</small></article>)}</div>{values.length === 0 && <p className="evaluation-empty">等待逐题结果</p>}</section>;
}

function EvaluationItemRow({
  item,
  loadingTrace,
  onOpenTrace,
}: {
  item: EvaluationItem;
  loadingTrace: boolean;
  onOpenTrace: () => void;
}) {
  return (
    <tr>
      <td>
        <details>
          <summary><strong>{item.question_id}</strong><span>{item.question}</span></summary>
          <div className="evaluation-item-detail">
            <ol>{item.options.map((option) => <li key={option.letter}><b>{option.letter}</b>{option.text}</li>)}</ol>
            {item.reasoning_summary && <p><b>模型依据：</b>{item.reasoning_summary}</p>}
            {item.error_message && <p className="is-error"><b>错误：</b>{item.error_message}</p>}
            <small>case {item.case_id} · 置信度 {item.confidence ?? "—"} · Token {item.input_tokens + item.output_tokens}</small>
          </div>
        </details>
      </td>
      <td>{item.benchmark_year}<small>{item.category}</small></td>
      <td><strong>{item.predicted_answer ?? "—"}</strong></td>
      <td><strong>{item.correct_answer}</strong></td>
      <td>{item.status === "pending" ? <span className="evaluation-result is-pending"><Clock3 size={12} />等待</span> : item.status === "running" ? <span className="evaluation-result is-running"><LoaderCircle className="spin" size={12} />生成中</span> : item.status === "error" ? <span className="evaluation-result is-error"><AlertCircle size={12} />错误</span> : item.is_correct ? <span className="evaluation-result is-correct"><CheckCircle2 size={12} />正确</span> : <span className="evaluation-result is-wrong"><XCircle size={12} />答错</span>}</td>
      <td>{item.latency_ms === null ? "—" : `${(item.latency_ms / 1000).toFixed(1)}秒`}</td>
      <td>
        <button
          className="evaluation-trace-button"
          type="button"
          disabled={item.status === "pending" || item.status === "running" || loadingTrace}
          onClick={onOpenTrace}
        >
          {loadingTrace ? <LoaderCircle className="spin" size={13} /> : <Workflow size={13} />}
          {loadingTrace ? "读取中" : "查看"}
        </button>
      </td>
    </tr>
  );
}

function EvaluationTraceModal({
  trace,
  onClose,
}: {
  trace: EvaluationItemTrace;
  onClose: () => void;
}) {
  const protocolLabel = trace.api_protocol === "responses"
    ? "OpenAI Responses"
    : trace.api_protocol === "chat_completions"
      ? "OpenAI Chat Completions"
      : "—";
  return (
    <AgentDebugModal
      apiProtocol={trace.api_protocol}
      protocolLabel={protocolLabel}
      model={trace.model}
      modelCallCount={trace.model_calls.length}
      toolExecutionCount={trace.tool_executions.length}
      endpoint={trace.endpoint}
      modelCalls={trace.model_calls}
      redacted={trace.redacted}
      footerPrefix={`题目：${trace.question_id} · Prompt SHA-256：${trace.prompt_sha256 ?? "—"}`}
      onClose={onClose}
    />
  );
}
