"use client";

import { X } from "lucide-react";
import { useEffect, useMemo } from "react";

interface DebugModelCall {
  sequence: number;
  stage: string;
  request_body: Record<string, unknown>;
  response_body: Record<string, unknown>;
}

type HistoryRole = "system" | "user" | "reason" | "assistant" | "tool";

interface HistoryItem {
  id: string;
  role: HistoryRole;
  label: string;
  source: string;
  content: unknown;
  state?: "history" | "response" | "encrypted";
}

const ROLE_LABELS: Record<HistoryRole, string> = {
  system: "System",
  user: "User",
  reason: "Reason",
  assistant: "Assistant",
  tool: "Tool",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function visibleContent(value: unknown): unknown {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return value;
  const texts = value
    .map(asRecord)
    .filter((item): item is Record<string, unknown> => item !== null)
    .map((item) => item.text)
    .filter((text): text is string => typeof text === "string");
  return texts.length > 0 ? texts.join("\n") : value;
}

function reasoningSummary(item: Record<string, unknown>): unknown {
  const summary = asArray(item.summary)
    .map(asRecord)
    .filter((value): value is Record<string, unknown> => value !== null)
    .map((value) => value.text)
    .filter((text): text is string => typeof text === "string" && text.length > 0);
  if (summary.length > 0) return summary.join("\n");
  if (typeof item.reasoning_content === "string") return item.reasoning_content;
  if (typeof item.reasoning === "string") return item.reasoning;
  if (typeof item.thinking === "string") return item.thinking;
  return item.encrypted_content ? "加密推理内容（不可直接展示）" : item;
}

function responseOutput(response: Record<string, unknown>): unknown[] {
  return asArray(response.output);
}

function sameJson(left: unknown, right: unknown): boolean {
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function responsesHistory(modelCalls: DebugModelCall[]): HistoryItem[] {
  if (modelCalls.length === 0) return [];
  const items: HistoryItem[] = [];
  const firstRequest = modelCalls[0].request_body;
  const instructions = firstRequest.instructions;
  if (typeof instructions === "string" && instructions.length > 0) {
    items.push({
      id: "responses-system",
      role: "system",
      label: "System",
      source: "请求体 1 · instructions",
      content: instructions,
    });
  }

  for (const [index, value] of asArray(firstRequest.input).entries()) {
    const input = asRecord(value);
    if (!input || input.role !== "user") continue;
    items.push({
      id: `responses-user-${index}`,
      role: "user",
      label: "User",
      source: "请求体 1",
      content: visibleContent(input.content),
    });
  }

  const toolNames = new Map<string, string>();
  for (const [callIndex, call] of modelCalls.entries()) {
    const nextRequest = modelCalls[callIndex + 1]?.request_body;
    const currentInputLength = asArray(call.request_body.input).length;
    const nextSuffix = nextRequest
      ? asArray(nextRequest.input).slice(currentInputLength)
      : [];
    const carriedIds = new Set(
      nextSuffix
        .map(asRecord)
        .filter((value): value is Record<string, unknown> => value !== null)
        .map((value) => value.id)
        .filter((id): id is string => typeof id === "string"),
    );
    const outputs = responseOutput(call.response_body);

    for (const [outputIndex, rawOutput] of outputs.entries()) {
      const output = asRecord(rawOutput);
      if (!output) continue;
      const outputId = typeof output.id === "string" ? output.id : null;
      const enteredHistory = Boolean(nextRequest && (
        (outputId && carriedIds.has(outputId))
        || nextSuffix.some((value) => sameJson(value, output))
      ));

      if (output.type === "reasoning") {
        const encrypted = Boolean(output.encrypted_content)
          && asArray(output.summary).length === 0;
        items.push({
          id: `responses-reason-${call.sequence}-${outputIndex}`,
          role: "reason",
          label: "Reason",
          source: `原始响应 ${call.sequence}`,
          content: reasoningSummary(output),
          state: encrypted ? "encrypted" : enteredHistory ? "history" : "response",
        });
        continue;
      }

      if (output.type === "function_call") {
        const callId = typeof output.call_id === "string" ? output.call_id : "";
        const name = typeof output.name === "string" ? output.name : "未知工具";
        if (callId) toolNames.set(callId, name);
        items.push({
          id: `responses-assistant-tool-${call.sequence}-${outputIndex}`,
          role: "assistant",
          label: `Assistant · ${name}`,
          source: `原始响应 ${call.sequence}`,
          content: {
            call_id: output.call_id,
            name: output.name,
            arguments: output.arguments,
          },
          state: enteredHistory ? "history" : "response",
        });
        continue;
      }

      if (output.type === "message") {
        items.push({
          id: `responses-assistant-${call.sequence}-${outputIndex}`,
          role: "assistant",
          label: "Assistant",
          source: `原始响应 ${call.sequence}`,
          content: visibleContent(output.content),
          state: enteredHistory ? "history" : "response",
        });
      }
    }

    if (outputs.length === 0 && typeof call.response_body.output_text === "string") {
      items.push({
        id: `responses-assistant-direct-${call.sequence}`,
        role: "assistant",
        label: "Assistant",
        source: `原始响应 ${call.sequence}`,
        content: call.response_body.output_text,
        state: "response",
      });
    }

    for (const [suffixIndex, rawValue] of nextSuffix.entries()) {
      const value = asRecord(rawValue);
      if (!value || value.type !== "function_call_output") continue;
      const callId = typeof value.call_id === "string" ? value.call_id : "";
      const name = toolNames.get(callId) ?? "未知工具";
      items.push({
        id: `responses-tool-${call.sequence}-${suffixIndex}`,
        role: "tool",
        label: `Tool · ${name}`,
        source: `请求体 ${call.sequence + 1}`,
        content: value.output,
        state: "history",
      });
    }
  }
  return items;
}

function chatReasoning(message: Record<string, unknown>): string | null {
  for (const key of ["reasoning_content", "reasoning", "thinking"]) {
    const value = message[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

function chatHistory(modelCalls: DebugModelCall[]): HistoryItem[] {
  if (modelCalls.length === 0) return [];
  const items: HistoryItem[] = [];
  const initialMessages = asArray(modelCalls[0].request_body.messages);
  for (const [index, rawMessage] of initialMessages.entries()) {
    const message = asRecord(rawMessage);
    if (!message || (message.role !== "system" && message.role !== "user")) continue;
    const role = message.role as "system" | "user";
    items.push({
      id: `chat-${role}-${index}`,
      role,
      label: ROLE_LABELS[role],
      source: "请求体 1",
      content: visibleContent(message.content),
    });
  }

  for (const [callIndex, call] of modelCalls.entries()) {
    const choices = asArray(call.response_body.choices);
    const choice = asRecord(choices[0]);
    const message = asRecord(choice?.message);
    if (!message) continue;
    const nextRequest = modelCalls[callIndex + 1]?.request_body;
    const currentMessageLength = asArray(call.request_body.messages).length;
    const nextSuffix = nextRequest
      ? asArray(nextRequest.messages).slice(currentMessageLength)
      : [];
    const nextAssistant = nextSuffix
      .map(asRecord)
      .find((value) => value?.role === "assistant") ?? null;
    const reason = chatReasoning(message);
    if (reason) {
      items.push({
        id: `chat-reason-${call.sequence}`,
        role: "reason",
        label: "Reason",
        source: `原始响应 ${call.sequence}`,
        content: reason,
        state: nextAssistant && chatReasoning(nextAssistant) === reason
          ? "history"
          : "response",
      });
    }

    const toolCalls = asArray(message.tool_calls);
    if (typeof message.content === "string" && message.content.trim().length > 0) {
      items.push({
        id: `chat-assistant-text-${call.sequence}`,
        role: "assistant",
        label: "Assistant",
        source: `原始响应 ${call.sequence}`,
        content: message.content,
        state: nextRequest ? "history" : "response",
      });
    }
    if (toolCalls.length > 0) {
      items.push({
        id: `chat-assistant-tools-${call.sequence}`,
        role: "assistant",
        label: "Assistant · Tool Calls",
        source: `原始响应 ${call.sequence}`,
        content: toolCalls,
        state: nextRequest ? "history" : "response",
      });
    }

    for (const [suffixIndex, rawValue] of nextSuffix.entries()) {
      const value = asRecord(rawValue);
      if (!value || value.role !== "tool") continue;
      const name = typeof value.name === "string" ? value.name : "未知工具";
      items.push({
        id: `chat-tool-${call.sequence}-${suffixIndex}`,
        role: "tool",
        label: `Tool · ${name}`,
        source: `请求体 ${call.sequence + 1}`,
        content: value.content,
        state: "history",
      });
    }
  }
  return items;
}

function historyFor(protocol: string, modelCalls: DebugModelCall[]): HistoryItem[] {
  return protocol === "responses"
    ? responsesHistory(modelCalls)
    : chatHistory(modelCalls);
}

function displayValue(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2) ?? String(value);
}

function stateLabel(state: HistoryItem["state"]): string | null {
  if (state === "encrypted") return "加密内容";
  return null;
}

export function AgentDebugModal({
  apiProtocol,
  protocolLabel,
  model,
  modelCallCount,
  toolExecutionCount,
  endpoint,
  modelCalls,
  redacted,
  footerPrefix,
  onClose,
}: {
  apiProtocol: string;
  protocolLabel: string;
  model: string;
  modelCallCount: number;
  toolExecutionCount: number;
  endpoint: string;
  modelCalls: DebugModelCall[];
  redacted: string[];
  footerPrefix?: string;
  onClose: () => void;
}) {
  const history = useMemo(
    () => historyFor(apiProtocol, modelCalls),
    [apiProtocol, modelCalls],
  );

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

          <div className="agent-debug-columns">
            <section className="agent-debug-column" aria-labelledby="agent-raw-title">
              <h5 id="agent-raw-title">原始内容</h5>
              <div className="agent-debug-column-scroll agent-debug-details">
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
            </section>

            <section className="agent-debug-column" aria-labelledby="agent-history-title">
              <h5 id="agent-history-title">History</h5>
              <div className="agent-debug-column-scroll agent-debug-details">
                {history.map((item) => {
                  const status = stateLabel(item.state);
                  return (
                    <details key={item.id}>
                      <summary>
                        {item.label} · {item.source}{status ? ` · ${status}` : ""}
                      </summary>
                      <pre>{displayValue(item.content)}</pre>
                    </details>
                  );
                })}
                {history.length === 0 && <p>没有可解析的 History。</p>}
              </div>
            </section>
          </div>

          <p className="agent-debug-redacted">
            {footerPrefix ? `${footerPrefix} · ` : ""}已隐藏：{redacted.join("、")}
          </p>
        </div>
      </section>
    </div>
  );
}
