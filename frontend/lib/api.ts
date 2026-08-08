import type {
  ChartPreview,
  ChartPreviewRequest,
  ModelConnectionTestRequest,
  ModelConnectionTestResponse,
  ModelSettings,
  ModelSettingsUpdate,
  ReportGenerationResponse,
} from "@/lib/types";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const REQUEST_TIMEOUT_MS = 15_000;
const REPORT_TIMEOUT_MS = 120_000;

interface FastApiValidationError {
  detail?: string | Array<{ loc?: Array<string | number>; msg?: string }>;
}

function getErrorMessage(payload: FastApiValidationError | null, status: number): string {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }

  if (Array.isArray(payload?.detail)) {
    const messages = payload.detail.flatMap((item) => (item.msg ? [item.msg] : []));
    if (messages.length > 0) return messages.join("；");
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
    const response = await fetch(`${API_BASE_URL}${path}`, { ...init, signal: controller.signal });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as FastApiValidationError | null;
      throw new Error(getErrorMessage(payload, response.status));
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
