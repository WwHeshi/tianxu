"use client";

import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { useEffect } from "react";

interface DebugStep {
  id: string;
  category: "deterministic" | "context" | "prompt" | "model" | "tool" | "validation";
  status: "completed" | "failed";
  title: string;
  detail: string;
  duration_ms: number | null;
}

interface DebugModelCall {
  sequence: number;
  stage: string;
  request_body: Record<string, unknown>;
  response_body: Record<string, unknown>;
}

export function AgentDebugModal({
  protocolLabel,
  model,
  modelCallCount,
  toolExecutionCount,
  endpoint,
  steps,
  systemPrompt,
  userPrompt,
  modelCalls,
  redacted,
  footerPrefix,
  onClose,
}: {
  protocolLabel: string;
  model: string;
  modelCallCount: number;
  toolExecutionCount: number;
  endpoint: string;
  steps: DebugStep[];
  systemPrompt: string | null;
  userPrompt: string | null;
  modelCalls: DebugModelCall[];
  redacted: string[];
  footerPrefix?: string;
  onClose: () => void;
}) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop agent-debug-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="agent-debug-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-debug-title"
      >
        <div className="agent-debug-panel">
          <div className="agent-debug-heading">
            <div>
              <p className="eyebrow">DEBUG TRACE</p>
              <h4 id="agent-debug-title">Agent 执行链路</h4>
            </div>
            <div className="agent-debug-heading-actions">
              <button
                className="icon-button"
                type="button"
                onClick={onClose}
                aria-label="关闭执行链路"
              >
                <X size={17} aria-hidden="true" />
              </button>
            </div>
          </div>

          <dl className="agent-trace-meta">
            <div><dt>协议</dt><dd>{protocolLabel}</dd></div>
            <div><dt>模型</dt><dd title={model}>{model}</dd></div>
            <div><dt>模型调用</dt><dd>{modelCallCount} 次</dd></div>
            <div><dt>工具执行</dt><dd>{toolExecutionCount} 次</dd></div>
            <div><dt>地址</dt><dd title={endpoint}>{endpoint}</dd></div>
          </dl>

          <ol className="agent-trace-timeline">
            {steps.map((step, index) => (
              <li key={step.id} data-category={step.category} data-status={step.status}>
                <span className="agent-trace-marker">
                  {step.status === "failed"
                    ? <AlertCircle size={15} aria-hidden="true" />
                    : <CheckCircle2 size={15} aria-hidden="true" />}
                </span>
                <div>
                  <div className="agent-trace-title">
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{step.title}</strong>
                    {step.duration_ms !== null && <small>{step.duration_ms} ms</small>}
                  </div>
                  <p>{step.detail}</p>
                </div>
              </li>
            ))}
          </ol>

          <div className="agent-debug-details">
            <details>
              <summary>系统提示词</summary>
              <pre>{systemPrompt ?? "该记录没有可用的系统提示词。"}</pre>
            </details>
            <details>
              <summary>用户提示词</summary>
              <pre>{userPrompt ?? "该记录没有可用的用户提示词。"}</pre>
            </details>
            {modelCalls.map((call) => (
              <div key={`${call.sequence}-${call.stage}`} className="agent-debug-model-pair">
                <details>
                  <summary>请求体 {call.sequence}</summary>
                  <pre>{JSON.stringify(call.request_body, null, 2)}</pre>
                </details>
                <details>
                  <summary>原始响应 {call.sequence}</summary>
                  <pre>{JSON.stringify(call.response_body, null, 2)}</pre>
                </details>
              </div>
            ))}
          </div>

          <p className="agent-debug-redacted">
            {footerPrefix ? `${footerPrefix} · ` : ""}已隐藏：{redacted.join("、")}
          </p>
        </div>
      </section>
    </div>
  );
}
