import type {
  AgentDebugTrace,
  AdminUserCreate,
  AdminUserUpdate,
  ChartPreview,
  ChartPreviewRequest,
  BootstrapStatus,
  CurrentUser,
  LoginResponse,
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
