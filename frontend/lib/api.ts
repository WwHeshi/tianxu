import type {
  AgentDebugTrace,
  AgentConversationDetail,
  AgentConversationList,
  AgentConversationStreamEvent,
  AgentConversationTrace,
  AdminUserCreate,
  AdminUserUpdate,
  ChartPreview,
  ChartPreviewRequest,
  EvaluationItemList,
  EvaluationItemTrace,
  EvaluationOverview,
  EvaluationRunDetail,
  EvaluationRunList,
  EvaluationStartRequest,
  BootstrapStatus,
  CurrentUser,
  GraphOrganizingJob,
  GraphOrganizingJobList,
  GraphOrganizingTrace,
  GraphOrganizingTraceList,
  LoginResponse,
  KnowledgeDocument,
  KnowledgeDocumentContent,
  KnowledgeDocumentList,
  KnowledgeGraphStatus,
  KnowledgeGraphSnapshot,
  ModelConnectionTestRequest,
  ModelConnectionTestResponse,
  ModelSettings,
  ModelSettingsUpdate,
  ReportGenerationResponse,
  UserListResponse,
} from "@/lib/types";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const REQUEST_TIMEOUT_MS = 15_000;
const REPORT_TIMEOUT_MS = 120_000;

interface FastApiValidationError {
  detail?:
    | string
    | Array<{ loc?: Array<string | number>; msg?: string }>
    | { message?: string; debug_trace?: AgentDebugTrace };
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class ReportGenerationError extends ApiError {
  readonly debugTrace: AgentDebugTrace;

  constructor(message: string, status: number, debugTrace: AgentDebugTrace) {
    super(message, status);
    this.name = "ReportGenerationError";
    this.debugTrace = debugTrace;
  }
}

function getErrorMessage(payload: FastApiValidationError | null, status: number): string {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }

  if (Array.isArray(payload?.detail)) {
    const messages = payload.detail.flatMap((item) => (item.msg ? [item.msg] : []));
    if (messages.length > 0) return messages.join("；");
  }

  if (payload?.detail && !Array.isArray(payload.detail) && typeof payload.detail === "object") {
    if (typeof payload.detail.message === "string") return payload.detail.message;
  }

  if (status >= 500) return "服务暂时不可用，请稍后再试。";
  return "出生信息未通过校验，请检查后重试。";
}

async function requestJson<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      credentials: "include",
      signal: controller.signal,
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as FastApiValidationError | null;
      const message = getErrorMessage(payload, response.status);
      if (
        payload?.detail
        && !Array.isArray(payload.detail)
        && typeof payload.detail === "object"
        && payload.detail.debug_trace
      ) {
        throw new ReportGenerationError(message, response.status, payload.detail.debug_trace);
      }
      throw new ApiError(message, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("服务响应超时，请检查网络后重试。");
    }
    if (error instanceof TypeError) {
      throw new Error("无法连接后端服务，请确认后端已经启动。");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function login(username: string, password: string): Promise<LoginResponse> {
  return requestJson<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function getBootstrapStatus(): Promise<BootstrapStatus> {
  return requestJson<BootstrapStatus>("/api/v1/auth/bootstrap-status", { method: "GET" });
}

export function bootstrapAdmin(input: {
  username: string;
  display_name: string;
  password: string;
}): Promise<LoginResponse> {
  return requestJson<LoginResponse>("/api/v1/auth/bootstrap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function logout(): Promise<void> {
  return requestJson<void>("/api/v1/auth/logout", { method: "POST" });
}

export function getCurrentUser(): Promise<CurrentUser> {
  return requestJson<CurrentUser>("/api/v1/auth/me", { method: "GET" });
}

export function listAgentConversations(): Promise<AgentConversationList> {
  return requestJson<AgentConversationList>("/api/v1/chat/conversations", { method: "GET" });
}

export function createAgentConversation(
  birthInput: ChartPreviewRequest | null = null,
): Promise<AgentConversationDetail> {
  return requestJson<AgentConversationDetail>("/api/v1/chat/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ birth_input: birthInput }),
  });
}

export function getAgentConversation(conversationId: string): Promise<AgentConversationDetail> {
  return requestJson<AgentConversationDetail>(
    `/api/v1/chat/conversations/${conversationId}`,
    { method: "GET" },
  );
}

export async function streamAgentConversationMessage(
  conversationId: string,
  content: string,
  onEvent: (event: AgentConversationStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}/api/v1/chat/conversations/${conversationId}/messages`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/x-ndjson",
        },
        body: JSON.stringify({ content }),
        signal,
      },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new Error("无法连接后端服务，请确认后端已经启动。");
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as FastApiValidationError | null;
    throw new ApiError(getErrorMessage(payload, response.status), response.status);
  }
  if (!response.body) throw new Error("浏览器没有收到可读取的流式响应。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      onEvent(JSON.parse(trimmed) as AgentConversationStreamEvent);
    }
    if (done) break;
  }
  if (buffer.trim()) {
    onEvent(JSON.parse(buffer) as AgentConversationStreamEvent);
  }
}

export function getAgentConversationTrace(
  conversationId: string,
  messageId: number,
): Promise<AgentConversationTrace> {
  return requestJson<AgentConversationTrace>(
    `/api/v1/chat/conversations/${conversationId}/messages/${messageId}/trace`,
    { method: "GET" },
  );
}

export function deleteAgentConversation(conversationId: string): Promise<void> {
  return requestJson<void>(`/api/v1/chat/conversations/${conversationId}`, {
    method: "DELETE",
  });
}

export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<CurrentUser> {
  return requestJson<CurrentUser>("/api/v1/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export function listUsers(offset = 0, limit = 50): Promise<UserListResponse> {
  return requestJson<UserListResponse>(`/api/v1/admin/users?offset=${offset}&limit=${limit}`, {
    method: "GET",
  });
}

export function createUser(input: AdminUserCreate): Promise<CurrentUser> {
  return requestJson<CurrentUser>("/api/v1/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function updateUser(userId: string, input: AdminUserUpdate): Promise<CurrentUser> {
  return requestJson<CurrentUser>(`/api/v1/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function resetUserPassword(userId: string, newPassword: string): Promise<void> {
  return requestJson<void>(`/api/v1/admin/users/${userId}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export function revokeUserSessions(userId: string): Promise<void> {
  return requestJson<void>(`/api/v1/admin/users/${userId}/revoke-sessions`, {
    method: "POST",
  });
}

export function listKnowledgeDocuments(search = ""): Promise<KnowledgeDocumentList> {
  const query = new URLSearchParams({ offset: "0", limit: "200" });
  if (search.trim()) query.set("search", search.trim());
  return requestJson<KnowledgeDocumentList>(
    `/api/v1/admin/knowledge/documents?${query.toString()}`,
    { method: "GET" },
  );
}

export function getKnowledgeGraphStatus(): Promise<KnowledgeGraphStatus> {
  return requestJson<KnowledgeGraphStatus>("/api/v1/admin/graph/status", {
    method: "GET",
  });
}

export function getKnowledgeGraphSnapshot(): Promise<KnowledgeGraphSnapshot> {
  return requestJson<KnowledgeGraphSnapshot>("/api/v1/admin/graph", {
    method: "GET",
  });
}

export function listGraphOrganizingJobs(): Promise<GraphOrganizingJobList> {
  return requestJson<GraphOrganizingJobList>("/api/v1/admin/graph/jobs", {
    method: "GET",
  });
}

export function listGraphOrganizingTraces(
  jobId: string,
): Promise<GraphOrganizingTraceList> {
  return requestJson<GraphOrganizingTraceList>(
    `/api/v1/admin/graph/jobs/${jobId}/traces`,
    { method: "GET" },
  );
}

export function getGraphOrganizingTrace(
  jobId: string,
  traceId: number,
): Promise<GraphOrganizingTrace> {
  return requestJson<GraphOrganizingTrace>(
    `/api/v1/admin/graph/jobs/${jobId}/traces/${traceId}`,
    { method: "GET" },
  );
}

export function startGraphOrganizingJob(
  documentId: string,
): Promise<GraphOrganizingJob> {
  return requestJson<GraphOrganizingJob>("/api/v1/admin/graph/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_id: documentId }),
  });
}

export function pauseGraphOrganizingJob(
  jobId: string,
): Promise<GraphOrganizingJob> {
  return requestJson<GraphOrganizingJob>(
    `/api/v1/admin/graph/jobs/${jobId}/pause`,
    { method: "POST" },
  );
}

export function resumeGraphOrganizingJob(
  jobId: string,
): Promise<GraphOrganizingJob> {
  return requestJson<GraphOrganizingJob>(
    `/api/v1/admin/graph/jobs/${jobId}/resume`,
    { method: "POST" },
  );
}

export function retryGraphOrganizingJob(
  jobId: string,
): Promise<GraphOrganizingJob> {
  return requestJson<GraphOrganizingJob>(
    `/api/v1/admin/graph/jobs/${jobId}/retry`,
    { method: "POST" },
  );
}

export function cancelGraphOrganizingJob(
  jobId: string,
): Promise<GraphOrganizingJob> {
  return requestJson<GraphOrganizingJob>(
    `/api/v1/admin/graph/jobs/${jobId}/cancel`,
    { method: "POST" },
  );
}

export function uploadKnowledgeDocument(
  file: File,
  title: string,
): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.set("file", file);
  if (title.trim()) form.set("title", title.trim());
  return requestJson<KnowledgeDocument>(
    "/api/v1/admin/knowledge/documents",
    { method: "POST", body: form },
    60_000,
  );
}

export function getKnowledgeDocumentContent(
  documentId: string,
  offset = 0,
  limit = 50_000,
): Promise<KnowledgeDocumentContent> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return requestJson<KnowledgeDocumentContent>(
    `/api/v1/admin/knowledge/documents/${documentId}/content?${query.toString()}`,
    { method: "GET" },
  );
}

export function deleteKnowledgeDocument(documentId: string): Promise<void> {
  return requestJson<void>(`/api/v1/admin/knowledge/documents/${documentId}`, {
    method: "DELETE",
  });
}

export async function downloadKnowledgeDocument(
  documentId: string,
  filename: string,
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/admin/knowledge/documents/${documentId}/download`,
    { credentials: "include" },
  );
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as FastApiValidationError | null;
    throw new ApiError(getErrorMessage(payload, response.status), response.status);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export function getEvaluationOverview(): Promise<EvaluationOverview> {
  return requestJson<EvaluationOverview>("/api/v1/admin/evaluations/overview", {
    method: "GET",
  });
}

export function listEvaluationRuns(): Promise<EvaluationRunList> {
  return requestJson<EvaluationRunList>("/api/v1/admin/evaluations/runs", {
    method: "GET",
  });
}

export function startEvaluationRun(
  input: EvaluationStartRequest,
): Promise<EvaluationRunDetail> {
  return requestJson<EvaluationRunDetail>("/api/v1/admin/evaluations/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function getEvaluationRun(runId: string): Promise<EvaluationRunDetail> {
  return requestJson<EvaluationRunDetail>(`/api/v1/admin/evaluations/runs/${runId}`, {
    method: "GET",
  });
}

export function deleteEvaluationRun(runId: string): Promise<void> {
  return requestJson<void>(`/api/v1/admin/evaluations/runs/${runId}`, {
    method: "DELETE",
  });
}

export function getEvaluationItems(
  runId: string,
  result?: "correct" | "incorrect" | "error",
): Promise<EvaluationItemList> {
  const query = result ? `?result=${result}` : "";
  return requestJson<EvaluationItemList>(
    `/api/v1/admin/evaluations/runs/${runId}/items${query}`,
    { method: "GET" },
  );
}

export function getEvaluationItemTrace(
  runId: string,
  itemId: number,
): Promise<EvaluationItemTrace> {
  return requestJson<EvaluationItemTrace>(
    `/api/v1/admin/evaluations/runs/${runId}/items/${itemId}/trace`,
    { method: "GET" },
  );
}

export function cancelEvaluationRun(runId: string): Promise<EvaluationRunDetail> {
  return requestJson<EvaluationRunDetail>(
    `/api/v1/admin/evaluations/runs/${runId}/cancel`,
    { method: "POST" },
  );
}

export async function downloadEvaluationExport(
  runId: string,
  format: "json" | "csv",
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/admin/evaluations/runs/${runId}/export?format=${format}`,
    { credentials: "include" },
  );
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as FastApiValidationError | null;
    throw new ApiError(getErrorMessage(payload, response.status), response.status);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = `mingli-evaluation-${runId}.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function previewChart(input: ChartPreviewRequest): Promise<ChartPreview> {
  const payload = await requestJson<ChartPreview>("/api/v1/charts/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!payload?.chart?.pillars || !payload?.normalized_input) {
    throw new Error("排盘服务返回了无法识别的数据，请稍后重试。");
  }
  return payload;
}

export function getModelSettings(): Promise<ModelSettings> {
  return requestJson<ModelSettings>("/api/v1/model-settings", { method: "GET" });
}

export function saveModelSettings(input: ModelSettingsUpdate): Promise<ModelSettings> {
  return requestJson<ModelSettings>("/api/v1/model-settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function testModelConnection(
  input: ModelConnectionTestRequest,
): Promise<ModelConnectionTestResponse> {
  return requestJson<ModelConnectionTestResponse>("/api/v1/model-settings/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function deleteModelSettings(): Promise<void> {
  return requestJson<void>("/api/v1/model-settings", { method: "DELETE" });
}

export function generateReport(input: ChartPreviewRequest): Promise<ReportGenerationResponse> {
  return requestJson<ReportGenerationResponse>(
    "/api/v1/reports/generate",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    REPORT_TIMEOUT_MS,
  );
}
