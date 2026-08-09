"use client";

import {
  AlertCircle,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { bootstrapAdmin, getBootstrapStatus } from "@/lib/api";

export function SetupAdminForm() {
  const [username, setUsername] = useState("admin");
  const [displayName, setDisplayName] = useState("管理员");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [isChecking, setIsChecking] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    void getBootstrapStatus()
      .then((status) => {
        if (!status.required) {
          window.location.replace("/login");
          return;
        }
        setIsChecking(false);
      })
      .catch((requestError) => {
        setError(requestError instanceof Error ? requestError.message : "无法读取初始化状态。");
        setIsChecking(false);
      });
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (username.trim().length < 3 || !displayName.trim()) {
      setError("请填写有效的管理员用户名和显示名称。");
      return;
    }
    if (password.length < 8) {
      setError("管理员密码至少需要 8 个字符。");
      return;
    }
    if (password !== confirmation) {
      setError("两次输入的密码不一致。");
      return;
    }
    setIsSubmitting(true);
    try {
      await bootstrapAdmin({
        username: username.trim(),
        display_name: displayName.trim(),
        password,
      });
      window.location.replace("/");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "管理员创建失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isChecking) {
    return <main className="auth-state-page"><LoaderCircle className="spin" size={24} /><p>正在检查系统状态</p></main>;
  }

  return (
    <main className="auth-page">
      <section className="auth-card setup-card" aria-labelledby="setup-title">
        <div className="auth-brand"><Sparkles size={18} /><span>天序</span></div>
        <div className="setup-badge"><ShieldCheck size={15} />首次启动</div>
        <p className="eyebrow">SYSTEM INITIALIZATION</p>
        <h1 id="setup-title">创建首位管理员</h1>
        <p className="auth-intro">系统中还没有账户。该管理员创建后，初始化入口将永久关闭。</p>
        <form onSubmit={handleSubmit}>
          <label className="auth-field">
            <span><UserRound size={15} />管理员用户名</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" maxLength={64} />
          </label>
          <label className="auth-field">
            <span><UserRound size={15} />显示名称</span>
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" maxLength={80} />
          </label>
          <label className="auth-field">
            <span><LockKeyhole size={15} />管理员密码</span>
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="new-password" maxLength={128} />
          </label>
          <label className="auth-field">
            <span><LockKeyhole size={15} />确认管理员密码</span>
            <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} type="password" autoComplete="new-password" maxLength={128} />
          </label>
          {error && <p className="form-message" role="alert"><AlertCircle size={15} />{error}</p>}
          <button className="primary-button auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting && <LoaderCircle className="spin" size={16} />}
            {isSubmitting ? "正在初始化" : "创建管理员并进入系统"}
          </button>
        </form>
      </section>
    </main>
  );
}
