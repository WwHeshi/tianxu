"use client";

import {
  AlertCircle,
  Bot,
  Clock3,
  LoaderCircle,
  MessageCircle,
  Plus,
  Send,
  Sparkles,
  Trash2,
  UserRound,
  Workflow,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AgentDebugModal } from "@/components/agent-debug-modal";
import { AppHeader } from "@/components/app-header";
import {
  ApiError,
  createAgentConversation,
  deleteAgentConversation,
  getAgentConversation,
  getAgentConversationTrace,
  getBootstrapStatus,
  getCurrentUser,
  listAgentConversations,
  logout,
  streamAgentConversationMessage,
} from "@/lib/api";
import type {
  AgentConversationDetail,
  AgentConversationTrace,
  AgentConversationSummary,
  CurrentUser,
} from "@/lib/types";

const TOOL_ACTIVITY: Record<string, string> = {
  calculate_bazi_chart: "正在读取命盘",
  calculate_fortune_at: "正在计算运势",
  search_rule_graph: "正在搜索规则图谱",
  query_rule_graph: "正在查询规则关系",
  search_knowledge: "正在搜索知识库",
  read_knowledge: "正在阅读资料原文",
};

function formatUpdatedAt(value: string): string {
  const date = new Date(value);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
  }).format(date);
}

function MarkdownAnswer({ content, streaming = false }: { content: string; streaming?: boolean }) {
  return (
    <div className={`chat-markdown${streaming ? " is-streaming" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) => (
            <a href={href} rel="noreferrer" target="_blank">{children}</a>
          ),
        }}
      >
        {content.trimStart()}
      </ReactMarkdown>
      {streaming && <span className="chat-stream-caret" aria-hidden="true" />}
    </div>
  );
}

export function ChatWorkspace() {
  const params = useParams<{ conversationId?: string[] }>();
  const router = useRouter();
  const conversationId = params.conversationId?.[0] ?? "";
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [conversations, setConversations] = useState<AgentConversationSummary[]>([]);
  const [conversation, setConversation] = useState<AgentConversationDetail | null>(null);
  const [content, setContent] = useState("");
  const [pendingContent, setPendingContent] = useState("");
  const [streamedContent, setStreamedContent] = useState("");
  const [streamActivity, setStreamActivity] = useState("正在思考");
  const [trace, setTrace] = useState<AgentConversationTrace | null>(null);
  const [traceLoadingId, setTraceLoadingId] = useState<number | null>(null);
  const [isGuardLoading, setIsGuardLoading] = useState(true);
  const [isListLoading, setIsListLoading] = useState(true);
  const [isConversationLoading, setIsConversationLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void getCurrentUser()
      .then((current) => {
        if (!active) return;
        setUser(current);
      })
      .catch((requestError) => {
        if (!active) return;
        if (requestError instanceof ApiError && requestError.status === 401) {
          void getBootstrapStatus()
            .then((status) => {
              window.location.replace(status.required ? "/setup" : "/login");
            })
            .catch(() => window.location.replace("/login"));
          return;
        }
        setError(requestError instanceof Error ? requestError.message : "无法读取登录状态。");
      })
      .finally(() => {
        if (active) setIsGuardLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!user) return;
    let active = true;
    setIsListLoading(true);
    void listAgentConversations()
      .then((response) => {
        if (active) setConversations(response.items);
      })
      .catch((requestError) => {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "无法读取对话列表。");
        }
      })
      .finally(() => {
        if (active) setIsListLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user]);

  useEffect(() => {
    if (!user) return;
    if (!conversationId) {
      setConversation(null);
      setError("");
      setIsConversationLoading(false);
      return;
    }
    let active = true;
    setConversation(null);
    setTrace(null);
    setIsConversationLoading(true);
    setError("");
    void getAgentConversation(conversationId)
      .then((detail) => {
        if (active) setConversation(detail);
      })
      .catch((requestError) => {
        if (!active) return;
        setConversation(null);
        setError(requestError instanceof Error ? requestError.message : "无法读取这段对话。");
      })
      .finally(() => {
        if (active) setIsConversationLoading(false);
      });
    return () => {
      active = false;
    };
  }, [conversationId, user]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: streamedContent ? "auto" : "smooth",
      block: "end",
    });
  }, [conversation?.messages, pendingContent, streamedContent]);

  async function refreshList() {
    const response = await listAgentConversations();
    setConversations(response.items);
  }

  async function handleSend(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const message = content.trim();
    if (!message || isSending) return;

    setContent("");
    setPendingContent(message);
    setStreamedContent("");
    setStreamActivity("正在结合命盘与资料思考");
    setError("");
    setIsSending(true);
    let createdConversationId = "";
    try {
      let activeConversation = conversation;
      if (activeConversation === null) {
        activeConversation = await createAgentConversation();
        createdConversationId = activeConversation.id;
        setConversation(activeConversation);
      }
      let completed = false;
      let streamFailure = "";
      await streamAgentConversationMessage(activeConversation.id, message, (streamEvent) => {
        if (streamEvent.type === "delta") {
          setStreamActivity("正在回答");
          setStreamedContent((current) => current + streamEvent.content);
        } else if (streamEvent.type === "reset") {
          setStreamedContent("");
          setStreamActivity("正在继续分析");
        } else if (streamEvent.type === "tool") {
          setStreamActivity(
            streamEvent.phase === "started"
              ? TOOL_ACTIVITY[streamEvent.name] ?? `正在调用 ${streamEvent.name}`
              : "资料已读取，正在继续思考",
          );
        } else if (streamEvent.type === "complete") {
          const turn = streamEvent.turn;
          completed = true;
          setConversation((current) => {
            const base = current?.id === activeConversation.id ? current : activeConversation;
            return {
              ...base,
              title: turn.title,
              updated_at: turn.updated_at,
              messages: [...base.messages, turn.user_message, turn.assistant_message],
            };
          });
          if (createdConversationId) router.replace(`/chat/${createdConversationId}`);
          void refreshList().catch(() => undefined);
        } else if (streamEvent.type === "error") {
          streamFailure = streamEvent.message;
        }
      });
      if (streamFailure) throw new Error(streamFailure);
      if (!completed) throw new Error("流式回答意外中断，请重试。");
    } catch (requestError) {
      if (createdConversationId) {
        void refreshList().catch(() => undefined);
      }
      setContent(message);
      setStreamedContent("");
      setError(requestError instanceof Error ? requestError.message : "消息发送失败，请重试。");
    } finally {
      setPendingContent("");
      setStreamedContent("");
      setStreamActivity("正在思考");
      setIsSending(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  function handleNewConversation() {
    if (isSending) return;
    setConversation(null);
    setContent("");
    setPendingContent("");
    setStreamedContent("");
    setTrace(null);
    setError("");
    if (conversationId) router.push("/chat");
  }

  async function handleOpenTrace(messageId: number) {
    if (!conversation || user?.role !== "admin" || traceLoadingId !== null) return;
    setTraceLoadingId(messageId);
    setError("");
    try {
      setTrace(await getAgentConversationTrace(conversation.id, messageId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法读取执行轨迹。");
    } finally {
      setTraceLoadingId(null);
    }
  }

  async function handleDelete(item: AgentConversationSummary) {
    if (!window.confirm(`确定删除“${item.title}”吗？对话记录将无法恢复。`)) return;
    try {
      await deleteAgentConversation(item.id);
      const remaining = conversations.filter((conversationItem) => conversationItem.id !== item.id);
      setConversations(remaining);
      if (conversationId === item.id) {
        setConversation(null);
        router.replace("/chat");
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法删除对话。");
    }
  }

  async function handleLogout() {
    await logout().catch(() => undefined);
    window.location.replace("/login");
  }

  if (isGuardLoading || !user) {
    if (error && !isGuardLoading) {
      return (
        <main className="auth-state-page">
          <AlertCircle size={24} />
          <section><h1>暂时无法进入命理对话</h1><p>{error}</p></section>
        </main>
      );
    }
    return (
      <main className="auth-state-page">
        <LoaderCircle className="spin" size={24} />
        <p>正在进入命理对话</p>
      </main>
    );
  }

  return (
    <main className="chat-page">
      <AppHeader
        currentUser={user}
        section="命理对话"
        onLogout={() => void handleLogout()}
        wide
      />

      <div className="chat-layout">
        <aside className="chat-sidebar">
          <button className="chat-new-button" type="button" onClick={handleNewConversation} disabled={isSending}>
            <Plus size={16} />新对话
          </button>
          <div className="chat-history-heading"><span>最近对话</span><small>{conversations.length}</small></div>
          <nav className="chat-history" aria-label="历史对话">
            {isListLoading && <div className="chat-sidebar-state"><LoaderCircle className="spin" size={17} />正在读取</div>}
            {!isListLoading && conversations.length === 0 && (
              <p className="chat-sidebar-empty">还没有对话记录</p>
            )}
            {conversations.map((item) => (
              <div className="chat-history-item" data-active={item.id === conversationId} key={item.id}>
                <button type="button" onClick={() => router.push(`/chat/${item.id}`)} disabled={isSending}>
                  <span>{item.has_chart ? <Sparkles size={14} /> : <MessageCircle size={14} />}{item.title}</span>
                  <small><Clock3 size={11} />{formatUpdatedAt(item.updated_at)}</small>
                </button>
                <button type="button" onClick={() => void handleDelete(item)} disabled={isSending} aria-label={`删除${item.title}`}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </nav>
        </aside>

        <section className="chat-main" aria-label="命理对话内容">
          {conversation?.chart && (
            <div className="chat-chart-context">
              <div>
                <span>当前命盘</span>
                <strong>{conversation.chart.year_pillar} · {conversation.chart.month_pillar} · {conversation.chart.day_pillar} · {conversation.chart.hour_pillar}</strong>
              </div>
              <dl>
                <div><dt>日主</dt><dd>{conversation.chart.day_master}</dd></div>
                <div><dt>性别</dt><dd>{conversation.chart.gender === "male" ? "男" : "女"}</dd></div>
                <div><dt>真太阳时</dt><dd>{conversation.chart.true_solar_datetime.replace("T", " ")}</dd></div>
                {conversation.chart.birthplace && <div><dt>出生地</dt><dd>{conversation.chart.birthplace}</dd></div>}
              </dl>
            </div>
          )}

          <div className="chat-messages">
            {isConversationLoading && (
              <div className="chat-loading"><LoaderCircle className="spin" size={22} /><span>正在读取对话</span></div>
            )}
            {!isConversationLoading && (!conversation || conversation.messages.length === 0) && !pendingContent && (
              <div className="chat-welcome">
                <span><Bot size={27} /></span>
                <h1>{conversation?.chart ? "想从这张命盘了解什么？" : "开始一段命理对话"}</h1>
                <p>{conversation?.chart
                  ? "可以连续追问命局、事业、关系，或指定某个年份与月份。"
                  : "可以询问一般命理知识；个性化分析建议先在排盘页生成命盘。"}</p>
              </div>
            )}
            {conversation?.messages.map((message) => (
              <article className={`chat-message is-${message.role}`} key={message.id}>
                <span>{message.role === "assistant" ? <Bot size={17} /> : <UserRound size={17} />}</span>
                <div className="chat-message-body">
                  {message.role === "assistant"
                    ? <MarkdownAnswer content={message.content} />
                    : <p>{message.content}</p>}
                  {message.role === "assistant" && user.role === "admin" && message.trace_available && (
                    <footer className="chat-message-actions">
                      <button
                        type="button"
                        onClick={() => void handleOpenTrace(message.id)}
                        disabled={traceLoadingId !== null}
                      >
                        {traceLoadingId === message.id
                          ? <LoaderCircle className="spin" size={13} />
                          : <Workflow size={13} />}
                        {traceLoadingId === message.id ? "正在读取" : "查看执行链路"}
                      </button>
                    </footer>
                  )}
                </div>
              </article>
            ))}
            {pendingContent && (
              <>
                <article className="chat-message is-user is-pending">
                  <span><UserRound size={17} /></span><div className="chat-message-body"><p>{pendingContent}</p></div>
                </article>
                <article className="chat-message is-assistant is-thinking">
                  <span><Bot size={17} /></span>
                  {streamedContent ? (
                    <div className="chat-message-body chat-streaming-answer" aria-live="polite">
                      <MarkdownAnswer content={streamedContent} streaming />
                      <small>{streamActivity}</small>
                    </div>
                  ) : (
                    <div className="chat-thinking-state">
                      <LoaderCircle className="spin" size={15} /><p>{streamActivity}</p>
                    </div>
                  )}
                </article>
              </>
            )}
            <div ref={messagesEndRef} />
          </div>

          <form className="chat-composer" onSubmit={(event) => void handleSend(event)}>
            {error && <div className="chat-error" role="alert"><AlertCircle size={15} />{error}</div>}
            <div>
              <textarea
                value={content}
                onChange={(event) => setContent(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                rows={3}
                placeholder={conversation?.chart ? "继续询问这张命盘…" : "输入你的问题…"}
                disabled={isSending || isConversationLoading}
                aria-label="对话消息"
              />
              <button type="submit" disabled={!content.trim() || isSending || isConversationLoading} aria-label="发送消息">
                {isSending ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}
              </button>
            </div>
            <small>Enter 发送，Shift + Enter 换行 · 内容仅供传统文化研究与参考</small>
          </form>
        </section>
      </div>
      {trace && (
        <AgentDebugModal
          apiProtocol={trace.api_protocol}
          protocolLabel={trace.api_protocol === "responses" ? "OpenAI Responses" : "OpenAI Chat Completions"}
          model={trace.model}
          modelCallCount={trace.model_calls.length}
          toolExecutionCount={trace.tool_executions.length}
          endpoint={trace.endpoint}
          modelCalls={trace.model_calls}
          redacted={trace.redacted}
          footerPrefix="聊天回答轨迹"
          onClose={() => setTrace(null)}
        />
      )}
    </main>
  );
}
