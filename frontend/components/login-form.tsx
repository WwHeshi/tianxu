"use client";

import { AlertCircle, LoaderCircle, LockKeyhole, Sparkles, UserRound } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { ApiError, getBootstrapStatus, getCurrentUser, login } from "@/lib/api";

export function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const status = await getBootstrapStatus();
        if (status.required) {
          window.location.replace("/setup");
          return;
        }
        const user = await getCurrentUser();
        window.location.replace(user.must_change_password ? "/change-password" : "/");
      } catch (requestError) {
        if (!(requestError instanceof ApiError) || requestError.status !== 401) {
          setError(requestError instanceof Error ? requestError.message : "无法连接登录服务。");
        }
      }
    })();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (username.trim().length < 3 || password.length < 8) {
      setError("请输入有效的用户名和密码。");
      return;
    }
    setIsSubmitting(true);
    try {
      const result = await login(username.trim(), password);
      window.location.replace(result.user.must_change_password ? "/change-password" : "/");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "登录失败，请稍后重试。");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-brand"><Sparkles size={18} /><span>天序</span></div>
        <p className="eyebrow">ACCOUNT ACCESS</p>
        <h1 id="login-title">登录系统</h1>
        <p className="auth-intro">使用管理员分配的账户进入八字排盘与分析服务。</p>
        <form onSubmit={handleSubmit}>
          <label className="auth-field">
            <span><UserRound size={15} />用户名</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              maxLength={64}
              autoFocus
            />
          </label>
          <label className="auth-field">
            <span><LockKeyhole size={15} />密码</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
              maxLength={128}
            />
          </label>
          {error && <p className="form-message" role="alert"><AlertCircle size={15} />{error}</p>}
          <button className="primary-button auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting && <LoaderCircle className="spin" size={16} />}
            {isSubmitting ? "正在登录" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
