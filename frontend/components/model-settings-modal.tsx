"use client";

import {
  AlertCircle,
  CheckCircle2,
  KeyRound,
  LoaderCircle,
  PlugZap,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import {
  deleteModelSettings,
  getModelSettings,
  saveModelSettings,
  testModelConnection,
} from "@/lib/api";
import type { ModelApiProtocol, ModelSettings } from "@/lib/types";

function normalizeModelBaseUrl(value: string): string {
  return value.trim().replace(/\/(responses|chat\/completions)\/?$/, "");
}

interface ModelSettingsModalProps {
  current: ModelSettings | null;
  onClose: () => void;
  onChange: (settings: ModelSettings) => void;
}

export function ModelSettingsModal({
  current,
  onClose,
  onChange,
}: ModelSettingsModalProps) {
  const [model, setModel] = useState(current?.model ?? "");
  const [apiProtocol, setApiProtocol] = useState<ModelApiProtocol>(
    current?.api_protocol ?? "responses",
  );
  const [baseUrl, setBaseUrl] = useState(current?.base_url ?? "https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [isLoading, setIsLoading] = useState(current === null);

  useEffect(() => {
    if (current === null) return;
    setModel(current.model ?? "");
    setApiProtocol(current.api_protocol ?? "responses");
    setBaseUrl(current.base_url ?? "https://api.openai.com/v1");
    setIsLoading(false);
  }, [current]);

  useEffect(() => {
    if (current !== null) return;
    let active = true;
    void getModelSettings()
      .then((settings) => {
        if (!active) return;
        onChange(settings);
        setModel(settings.model ?? "");
        setApiProtocol(settings.api_protocol ?? "responses");
        setBaseUrl(settings.base_url ?? "https://api.openai.com/v1");
      })
      .catch((loadError) => {
        if (active) {
          setErrorMessage(loadError instanceof Error ? loadError.message : "无法读取模型设置。");
        }
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [current, onChange]);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !isSaving && !isTesting) onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isSaving, isTesting, onClose]);

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    setStatusMessage("");
    if (!model.trim() || !baseUrl.trim() || !apiKey.trim()) {
      setErrorMessage("请填写模型 ID、Base URL 和新的 API 密钥。");
      return;
    }
    setIsSaving(true);
    try {
      const saved = await saveModelSettings({
        provider: "openai",
        api_protocol: apiProtocol,
        model: model.trim(),
        base_url: normalizeModelBaseUrl(baseUrl),
        api_key: apiKey.trim(),
      });
      onChange(saved);
      setApiKey("");
      setStatusMessage("设置已保存。关闭窗口后即可生成报告。");
    } catch (saveError) {
      setErrorMessage(saveError instanceof Error ? saveError.message : "保存失败，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleTestConnection() {
    setErrorMessage("");
    setStatusMessage("");
    if (!model.trim() || !baseUrl.trim()) {
      setErrorMessage("请先填写模型 ID 和 Base URL。");
      return;
    }
    if (!apiKey.trim() && !current?.configured) {
      setErrorMessage("请先输入 API 密钥。");
      return;
    }
    setIsTesting(true);
    try {
      const tested = await testModelConnection({
        provider: "openai",
        api_protocol: apiProtocol,
        model: model.trim(),
        base_url: normalizeModelBaseUrl(baseUrl),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setStatusMessage(tested.message);
    } catch (testError) {
      setErrorMessage(
        testError instanceof Error ? testError.message : "连接测试失败，请稍后重试。",
      );
    } finally {
      setIsTesting(false);
    }
  }

  async function handleDelete() {
    if (!window.confirm("确定清除当前模型设置吗？此操作不会显示或恢复原密钥。")) return;
    setErrorMessage("");
    setStatusMessage("");
    setIsSaving(true);
    try {
      await deleteModelSettings();
      onChange({
        configured: false,
        provider: null,
        api_protocol: null,
        model: null,
        base_url: null,
        api_key_masked: null,
      });
      setModel("");
      setApiKey("");
      setStatusMessage("模型设置已清除。");
    } catch (deleteError) {
      setErrorMessage(deleteError instanceof Error ? deleteError.message : "清除失败，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !isSaving && !isTesting) onClose();
    }}>
      <section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">MODEL CONNECTION</p>
            <h2 id="settings-title">模型 API 设置</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} disabled={isSaving || isTesting} aria-label="关闭设置">
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {isLoading ? (
          <div className="settings-loading"><LoaderCircle className="spin" size={20} /> 正在读取设置</div>
        ) : (
          <form className="settings-form" onSubmit={handleSave}>
            <div className="credential-status" data-configured={current?.configured || undefined}>
              <KeyRound size={17} aria-hidden="true" />
              <div>
                <strong>{current?.configured ? "API 密钥已配置" : "尚未配置 API 密钥"}</strong>
                <span>{current?.configured ? current.api_key_masked : "密钥保存后不会再次返回到浏览器"}</span>
              </div>
            </div>

            <label className="settings-field">
              <span>API 协议</span>
              <select
                value={apiProtocol}
                onChange={(event) => setApiProtocol(event.target.value as ModelApiProtocol)}
              >
                <option value="responses">OpenAI Responses API</option>
                <option value="chat_completions">OpenAI Chat Completions</option>
              </select>
              <small>
                请求地址：{normalizeModelBaseUrl(baseUrl) || "Base URL"}
                {apiProtocol === "responses" ? "/responses" : "/chat/completions"}
              </small>
            </label>
            <label className="settings-field">
              <span>模型 ID</span>
              <input
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="填写账户可用的模型 ID"
                autoComplete="off"
              />
            </label>
            <label className="settings-field">
              <span>Base URL</span>
              <input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.openai.com/v1"
                inputMode="url"
                autoComplete="url"
              />
            </label>
            <label className="settings-field">
              <span>{current?.configured ? "新 API 密钥（保存时替换）" : "API 密钥"}</span>
              <input
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={current?.configured ? "输入新密钥以替换现有密钥" : "输入 API 密钥"}
                type="password"
                autoComplete="new-password"
              />
            </label>

            <p className="settings-security-note">
              密钥会在服务端加密后存入 PostgreSQL；测试连接会发送一条极短请求，可能产生少量 token。
            </p>
            {errorMessage && <p className="form-message" role="alert"><AlertCircle size={15} /> {errorMessage}</p>}
            {statusMessage && <p className="settings-success" role="status"><CheckCircle2 size={15} /> {statusMessage}</p>}

            <div className="settings-actions">
              {current?.configured && (
                <button className="danger-button" type="button" onClick={handleDelete} disabled={isSaving || isTesting}>
                  <Trash2 size={15} /> 清除设置
                </button>
              )}
              <button className="test-button" type="button" onClick={handleTestConnection} disabled={isSaving || isTesting}>
                {isTesting ? <LoaderCircle className="spin" size={16} /> : <PlugZap size={16} />}
                {isTesting ? "正在测试" : "测试连接"}
              </button>
              <button className="primary-button settings-save" type="submit" disabled={isSaving || isTesting}>
                {isSaving ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                {isSaving ? "正在保存" : "保存"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
