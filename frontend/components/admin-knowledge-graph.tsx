"use client";

import {
  AlertCircle,
  Ban,
  Bot,
  CheckCircle2,
  Database,
  FileText,
  GitMerge,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  Search,
  Upload,
  Workflow,
} from "lucide-react";
import {
  DragEvent,
  FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import { AdminShell } from "@/components/admin-shell";
import { AgentDebugModal } from "@/components/agent-debug-modal";
import { KnowledgeGraphCanvas } from "@/components/knowledge-graph-canvas";
import { useAdminGuard } from "@/hooks/use-admin-guard";
import {
  cancelGraphOrganizingJob,
  getKnowledgeGraphSnapshot,
  getKnowledgeGraphStatus,
  getGraphOrganizingTrace,
  listGraphOrganizingJobs,
  listGraphOrganizingTraces,
  listKnowledgeDocuments,
  pauseGraphOrganizingJob,
  resumeGraphOrganizingJob,
  retryGraphOrganizingJob,
  startGraphOrganizingJob,
  uploadKnowledgeDocument,
} from "@/lib/api";
import type {
  GraphOrganizingJob,
  GraphOrganizingJobStatus,
  GraphOrganizingTrace,
  GraphOrganizingTraceSummary,
  KnowledgeDocument,
  KnowledgeGraphSnapshot,
  KnowledgeGraphStatus,
} from "@/lib/types";

const MAX_FILE_SIZE = 10 * 1024 * 1024;
const POLLING_JOB_STATUSES: GraphOrganizingJobStatus[] = [
  "queued",
  "analyzing",
  "pause_requested",
  "cancel_requested",
];
const UNFINISHED_JOB_STATUSES: GraphOrganizingJobStatus[] = [
  ...POLLING_JOB_STATUSES,
  "paused",
];
const CANCELLABLE_JOB_STATUSES: GraphOrganizingJobStatus[] = [
  "queued",
  "analyzing",
  "pause_requested",
  "paused",
  "cancel_requested",
];

const JOB_STATUS_LABELS: Record<GraphOrganizingJobStatus, string> = {
  queued: "排队中",
  analyzing: "正在分析",
  pause_requested: "正在暂停",
  paused: "已暂停",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  applied: "已自动融合",
  failed: "失败",
};

type GraphJobAction = "pause" | "resume" | "retry" | "cancel";

function titleFromFilename(filename: string): string {
  return filename.replace(/\.txt$/i, "");
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function isPollingJob(job: GraphOrganizingJob): boolean {
  return POLLING_JOB_STATUSES.includes(job.status);
}

function isUnfinishedJob(job: GraphOrganizingJob): boolean {
  return UNFINISHED_JOB_STATUSES.includes(job.status);
}

function jobProgress(job: GraphOrganizingJob): number {
  if (job.status === "applied") return 100;
  if (job.total_sections === 0) return 0;
  return Math.min(95, Math.round((job.processed_sections / job.total_sections) * 95));
}

function JobList({
  jobs,
  actingJob,
  loadingTraceJobId,
  onCancel,
  onOpenTrace,
  onPause,
  onResume,
  onRetry,
}: {
  jobs: GraphOrganizingJob[];
  actingJob: { jobId: string; action: GraphJobAction } | null;
  loadingTraceJobId: string | null;
  onCancel: (job: GraphOrganizingJob) => void;
  onOpenTrace: (job: GraphOrganizingJob) => void;
  onPause: (job: GraphOrganizingJob) => void;
  onResume: (job: GraphOrganizingJob) => void;
  onRetry: (job: GraphOrganizingJob) => void;
}) {
  if (jobs.length === 0) {
    return (
      <div className="graph-queue-empty">
        <GitMerge size={23} />
        <strong>还没有整理任务</strong>
        <p>选择一份 TXT 后，整理 Agent 会自动分析并融合到规则图谱。</p>
      </div>
    );
  }
  return (
    <div className="graph-job-list">
      {jobs.slice(0, 6).map((job) => {
        const progress = jobProgress(job);
        const actingAction = actingJob?.jobId === job.id ? actingJob.action : null;
        const hasPendingAction = actingJob !== null;
        return (
          <article className="graph-job" data-status={job.status} key={job.id}>
            <header>
              <strong title={job.document_title}>{job.document_title}</strong>
              <span>{JOB_STATUS_LABELS[job.status]}</span>
            </header>
            <div className="graph-job-progress" aria-label={`任务进度 ${progress}%`}>
              <i style={{ width: `${progress}%` }} />
            </div>
            <div className="graph-job-actions">
              <p>
                {job.status === "applied"
                  ? `新增 ${job.rules_created} · 合并 ${job.rules_merged} · 关系 ${job.relations_written}`
                  : `${job.processed_sections}/${job.total_sections || "-"} 段 · 已提取 ${job.rules_extracted} 条`}
              </p>
              <div className="graph-job-action-buttons">
                {(["queued", "analyzing", "pause_requested"] as GraphOrganizingJobStatus[])
                  .includes(job.status) && (
                  <button
                    className="graph-job-control-button"
                    type="button"
                    disabled={job.status === "pause_requested" || hasPendingAction}
                    onClick={() => onPause(job)}
                  >
                    {actingAction === "pause"
                      ? <LoaderCircle className="spin" size={12} />
                      : <Pause size={12} />}
                    {actingAction === "pause"
                      ? "处理中"
                      : job.status === "pause_requested" ? "暂停中" : "暂停"}
                  </button>
                )}
                {job.status === "paused" && (
                  <button
                    className="graph-job-control-button"
                    type="button"
                    disabled={hasPendingAction}
                    onClick={() => onResume(job)}
                  >
                    {actingAction === "resume"
                      ? <LoaderCircle className="spin" size={12} />
                      : <Play size={12} />}
                    {actingAction === "resume" ? "处理中" : "继续"}
                  </button>
                )}
                {job.status === "failed" && (
                  <button
                    className="graph-job-control-button"
                    type="button"
                    disabled={hasPendingAction}
                    onClick={() => onRetry(job)}
                  >
                    {actingAction === "retry"
                      ? <LoaderCircle className="spin" size={12} />
                      : <RotateCcw size={12} />}
                    {actingAction === "retry" ? "处理中" : "重试"}
                  </button>
                )}
                {CANCELLABLE_JOB_STATUSES.includes(job.status) && (
                  <button
                    className="graph-job-control-button is-danger"
                    type="button"
                    disabled={job.status === "cancel_requested" || hasPendingAction}
                    onClick={() => onCancel(job)}
                  >
                    {actingAction === "cancel"
                      ? <LoaderCircle className="spin" size={12} />
                      : <Ban size={12} />}
                    {actingAction === "cancel" || job.status === "cancel_requested"
                      ? "取消中"
                      : "取消"}
                  </button>
                )}
                <button
                  className="evaluation-trace-button"
                  type="button"
                  disabled={job.status === "queued" || loadingTraceJobId === job.id}
                  onClick={() => onOpenTrace(job)}
                >
                  {loadingTraceJobId === job.id
                    ? <LoaderCircle className="spin" size={12} />
                    : <Workflow size={12} />}
                  {loadingTraceJobId === job.id ? "读取中" : "查看轨迹"}
                </button>
              </div>
            </div>
            {job.failure_message && <small title={job.failure_message}>{job.failure_message}</small>}
          </article>
        );
      })}
    </div>
  );
}

function GraphTraceModal({
  trace,
  traces,
  isSwitching,
  onSelect,
  onClose,
}: {
  trace: GraphOrganizingTrace;
  traces: GraphOrganizingTraceSummary[];
  isSwitching: boolean;
  onSelect: (traceId: number) => void;
  onClose: () => void;
}) {
  const protocolLabel = trace.api_protocol === "responses"
    ? "OpenAI Responses"
    : trace.api_protocol === "chat_completions"
      ? "OpenAI Chat Completions"
      : trace.api_protocol;
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
      headerControls={(
        <label className="graph-trace-selector">
          <span>段落轨迹</span>
          <select
            value={trace.id}
            disabled={isSwitching}
            onChange={(event) => onSelect(Number(event.target.value))}
          >
            {traces.map((item) => (
              <option key={item.id} value={item.id}>
                第 {item.section_index + 1} 段 · 尝试 {item.attempt}
                {item.status === "failed" ? " · 失败" : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      footerPrefix={
        `资料：${trace.document_title} · 第 ${trace.section_index + 1} 段 · `
        + `字符 ${trace.start_offset}-${trace.end_offset} · 提取 ${trace.rules_extracted} 条`
      }
      onClose={onClose}
    />
  );
}

export function AdminKnowledgeGraph() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const appliedJobIdsRef = useRef<Set<string>>(new Set());
  const committedSectionsRef = useRef<Map<string, number>>(new Map());
  const { admin, isLoading: isGuardLoading, error: guardError } = useAdminGuard();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [graphStatus, setGraphStatus] = useState<KnowledgeGraphStatus | null>(null);
  const [graphSnapshot, setGraphSnapshot] = useState<KnowledgeGraphSnapshot | null>(null);
  const [jobs, setJobs] = useState<GraphOrganizingJob[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [traceJobId, setTraceJobId] = useState<string | null>(null);
  const [traceSummaries, setTraceSummaries] = useState<GraphOrganizingTraceSummary[]>([]);
  const [activeTrace, setActiveTrace] = useState<GraphOrganizingTrace | null>(null);
  const [loadingTraceJobId, setLoadingTraceJobId] = useState<string | null>(null);
  const [actingJob, setActingJob] = useState<{
    jobId: string;
    action: GraphJobAction;
  } | null>(null);
  const [isSwitchingTrace, setIsSwitchingTrace] = useState(false);
  const hasPollingJobs = jobs.some(isPollingJob);
  const selectedDocumentUnfinished = jobs.some(
    (job) => job.document_id === selectedDocumentId && isUnfinishedJob(job),
  );

  useEffect(() => {
    if (!admin) return () => undefined;
    let active = true;
    async function loadPage() {
      try {
        const [documentResponse, statusResponse, snapshotResponse, jobResponse] = await Promise.all([
          listKnowledgeDocuments(),
          getKnowledgeGraphStatus().catch(() => null),
          getKnowledgeGraphSnapshot().catch(() => null),
          listGraphOrganizingJobs(),
        ]);
        if (!active) return;
        setDocuments(documentResponse.items);
        setGraphStatus(statusResponse);
        setGraphSnapshot(snapshotResponse);
        setJobs(jobResponse.items);
        appliedJobIdsRef.current = new Set(
          jobResponse.items.filter((job) => job.status === "applied").map((job) => job.id),
        );
        committedSectionsRef.current = new Map(
          jobResponse.items.map((job) => [job.id, job.processed_sections]),
        );
        setSelectedDocumentId(documentResponse.items[0]?.id ?? "");
      } catch (requestError) {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "无法读取规则图谱。");
        }
      } finally {
        if (active) setIsLoading(false);
      }
    }
    void loadPage();
    return () => { active = false; };
  }, [admin]);

  useEffect(() => {
    if (!admin || !hasPollingJobs) return () => undefined;
    let active = true;
    async function pollJobs() {
      try {
        const response = await listGraphOrganizingJobs();
        if (!active) return;
        const hasNewAppliedJob = response.items.some(
          (job) => job.status === "applied" && !appliedJobIdsRef.current.has(job.id),
        );
        const hasNewCommittedSection = response.items.some(
          (job) => job.processed_sections > (committedSectionsRef.current.get(job.id) ?? 0),
        );
        if (hasNewCommittedSection || hasNewAppliedJob || !response.items.some(isPollingJob)) {
          const [statusResponse, snapshotResponse] = await Promise.all([
            getKnowledgeGraphStatus(),
            getKnowledgeGraphSnapshot(),
          ]);
          if (!active) return;
          setGraphStatus(statusResponse);
          setGraphSnapshot(snapshotResponse);
        }
        appliedJobIdsRef.current = new Set(
          response.items.filter((job) => job.status === "applied").map((job) => job.id),
        );
        committedSectionsRef.current = new Map(
          response.items.map((job) => [job.id, job.processed_sections]),
        );
        setJobs(response.items);
      } catch {
        // Keep the last visible state and try again on the next polling tick.
      }
    }
    const timer = window.setInterval(() => { void pollJobs(); }, 2_000);
    void pollJobs();
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [admin, hasPollingJobs]);

  function chooseFile(file: File | null) {
    setError("");
    setNotice("");
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".txt")) {
      setError("只支持上传 TXT 文件。");
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      setError("TXT 文件不能超过 10MB。");
      return;
    }
    setSelectedFile(file);
    setTitle(titleFromFilename(file.name));
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    chooseFile(event.dataTransfer.files[0] ?? null);
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!selectedFile) {
      setError("请先选择一个 TXT 文件。");
      return;
    }
    if (!title.trim()) {
      setError("请填写资料名称。");
      return;
    }

    setIsUploading(true);
    try {
      const created = await uploadKnowledgeDocument(selectedFile, title);
      setDocuments((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setSelectedDocumentId(created.id);
      setSelectedFile(null);
      setTitle("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      setNotice(`《${created.title}》已保存，可以开始自动整理。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "TXT 上传失败。");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleStartOrganizing() {
    if (!selectedDocumentId) return;
    setError("");
    setNotice("");
    setIsStarting(true);
    try {
      const job = await startGraphOrganizingJob(selectedDocumentId);
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      setNotice(`《${job.document_title}》已进入自动整理队列。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法启动自动整理。");
    } finally {
      setIsStarting(false);
    }
  }

  function replaceJob(updated: GraphOrganizingJob) {
    setJobs((current) => current.map((job) => job.id === updated.id ? updated : job));
  }

  async function handlePauseJob(job: GraphOrganizingJob) {
    setError("");
    setNotice("");
    setActingJob({ jobId: job.id, action: "pause" });
    try {
      const updated = await pauseGraphOrganizingJob(job.id);
      replaceJob(updated);
      setNotice(
        updated.status === "paused"
          ? `《${updated.document_title}》已暂停。`
          : `《${updated.document_title}》将在当前片段完成后暂停。`,
      );
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法暂停整理任务。");
    } finally {
      setActingJob(null);
    }
  }

  async function handleResumeJob(job: GraphOrganizingJob) {
    setError("");
    setNotice("");
    setActingJob({ jobId: job.id, action: "resume" });
    try {
      const updated = await resumeGraphOrganizingJob(job.id);
      replaceJob(updated);
      setNotice(`《${updated.document_title}》已继续执行。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法继续整理任务。");
    } finally {
      setActingJob(null);
    }
  }

  async function handleRetryJob(job: GraphOrganizingJob) {
    setError("");
    setNotice("");
    setActingJob({ jobId: job.id, action: "retry" });
    try {
      const updated = await retryGraphOrganizingJob(job.id);
      replaceJob(updated);
      setNotice(`《${updated.document_title}》将从上次进度继续重试。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法重试整理任务。");
    } finally {
      setActingJob(null);
    }
  }

  async function handleCancelJob(job: GraphOrganizingJob) {
    const confirmed = window.confirm(
      `确认取消《${job.document_title}》的整理任务吗？\n\n`
      + "任务会立即停止，已经写入规则图谱的内容会保留。取消后不能继续或重试。",
    );
    if (!confirmed) return;
    setError("");
    setNotice("");
    setActingJob({ jobId: job.id, action: "cancel" });
    try {
      const updated = await cancelGraphOrganizingJob(job.id);
      replaceJob(updated);
      setNotice(`《${updated.document_title}》的整理任务已取消。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法取消整理任务。");
    } finally {
      setActingJob(null);
    }
  }

  async function handleOpenTrace(job: GraphOrganizingJob) {
    setError("");
    setLoadingTraceJobId(job.id);
    try {
      const response = await listGraphOrganizingTraces(job.id);
      if (response.items.length === 0) {
        setError("这项任务还没有可查看的已完成调用轨迹。");
        return;
      }
      const latest = response.items[response.items.length - 1];
      const detail = await getGraphOrganizingTrace(job.id, latest.id);
      setTraceJobId(job.id);
      setTraceSummaries(response.items);
      setActiveTrace(detail);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法读取整理轨迹。");
    } finally {
      setLoadingTraceJobId(null);
    }
  }

  async function handleSelectTrace(traceId: number) {
    if (!traceJobId || activeTrace?.id === traceId) return;
    setError("");
    setIsSwitchingTrace(true);
    try {
      setActiveTrace(await getGraphOrganizingTrace(traceJobId, traceId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法切换整理轨迹。");
    } finally {
      setIsSwitchingTrace(false);
    }
  }

  function handleCloseTrace() {
    setActiveTrace(null);
    setTraceJobId(null);
    setTraceSummaries([]);
  }

  return (
    <AdminShell
      admin={admin}
      isLoading={isGuardLoading || isLoading}
      loadingText="正在读取规则图谱"
      sectionTitle="规则图谱"
      error={guardError}
      pageClassName="graph-page"
      layoutClassName="graph-layout"
      overlay={activeTrace && (
        <GraphTraceModal
          trace={activeTrace}
          traces={traceSummaries}
          isSwitching={isSwitchingTrace}
          onSelect={(traceId) => { void handleSelectTrace(traceId); }}
          onClose={handleCloseTrace}
        />
      )}
    >
      <section className="admin-heading graph-heading">
        <div><p className="eyebrow">NEO4J RULE GRAPH</p><h1>规则图谱</h1></div>
        <div className="graph-heading-statuses">
          <span data-connected={graphStatus?.connected || undefined}>
            <Database size={13} />Neo4j {graphStatus?.connected ? "已连接" : "连接不可用"}
          </span>
          <span><FileText size={13} />{documents.length} 份资料</span>
        </div>
      </section>

      {error && <p className="admin-message is-error" role="alert"><AlertCircle size={16} />{error}</p>}
      {notice && <p className="admin-message is-success" role="status"><CheckCircle2 size={16} />{notice}</p>}

      <div className="graph-workspace">
        <KnowledgeGraphCanvas status={graphStatus} snapshot={graphSnapshot} />

        <aside className="graph-agent-column">
          <section className="graph-agent-card" aria-labelledby="graph-upload-title">
            <div className="graph-card-title">
              <Upload size={18} />
              <div><h2 id="graph-upload-title">上传 TXT</h2><p>保存原文，随后交给整理 Agent 分析。</p></div>
            </div>
            <form className="graph-upload-form" onSubmit={handleUpload}>
              <label
                className="graph-dropzone"
                data-dragging={isDragging || undefined}
                onDragEnter={(event) => { event.preventDefault(); setIsDragging(true); }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={(event) => { event.preventDefault(); setIsDragging(false); }}
                onDrop={handleDrop}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,text/plain"
                  onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
                />
                <span className="graph-file-icon"><FileText size={20} /></span>
                <strong>{selectedFile ? selectedFile.name : "选择或拖入 TXT"}</strong>
                <small>{selectedFile ? formatFileSize(selectedFile.size) : "UTF-8、UTF-16、GB18030 · 最大 10MB"}</small>
              </label>
              <label className="graph-field">
                <span>资料名称</span>
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={200}
                  placeholder="选择文件后自动填写"
                />
              </label>
              <button type="submit" disabled={isUploading}>
                {isUploading ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}
                {isUploading ? "正在保存" : "保存 TXT"}
              </button>
            </form>
          </section>

          <section className="graph-agent-card" aria-labelledby="graph-agent-title">
            <div className="graph-card-title">
              <Bot size={18} />
              <div><h2 id="graph-agent-title">整理 Agent</h2><p>读取资料并自动融合进现有图谱。</p></div>
            </div>
            <label className="graph-field">
              <span>待整理资料</span>
              <select
                value={selectedDocumentId}
                onChange={(event) => setSelectedDocumentId(event.target.value)}
                disabled={documents.length === 0}
              >
                {documents.length === 0 && <option value="">暂无知识库资料</option>}
                {documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}
              </select>
            </label>
            <div className="graph-agent-flow" aria-label="整理流程">
              <span><Search size={14} /><b>阅读原文</b></span>
              <i />
              <span><GitMerge size={14} /><b>比对图谱</b></span>
              <i />
              <span><CheckCircle2 size={14} /><b>自动融合</b></span>
            </div>
            <button
              className="graph-organize-button"
              type="button"
              onClick={() => { void handleStartOrganizing(); }}
              disabled={
                !selectedDocumentId
                || isStarting
                || selectedDocumentUnfinished
                || graphStatus?.connected !== true
              }
            >
              {isStarting ? <LoaderCircle className="spin" size={15} /> : <Bot size={15} />}
              {isStarting
                ? "正在创建任务"
                : selectedDocumentUnfinished ? "这份资料已有整理任务" : "开始自动整理"}
            </button>
          </section>

          <section className="graph-queue-card" aria-labelledby="graph-queue-title">
            <header>
              <div><span className="graph-queue-dot" /><h2 id="graph-queue-title">整理任务</h2></div>
              <small>{jobs.length} 个任务</small>
            </header>
            <JobList
              jobs={jobs}
              actingJob={actingJob}
              loadingTraceJobId={loadingTraceJobId}
              onCancel={(job) => { void handleCancelJob(job); }}
              onOpenTrace={(job) => { void handleOpenTrace(job); }}
              onPause={(job) => { void handlePauseJob(job); }}
              onResume={(job) => { void handleResumeJob(job); }}
              onRetry={(job) => { void handleRetryJob(job); }}
            />
          </section>
        </aside>
      </div>
    </AdminShell>
  );
}
