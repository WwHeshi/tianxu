"use client";

import { AlertCircle, LoaderCircle, LockKeyhole, Sparkles } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { ApiError, changePassword, getCurrentUser, logout } from "@/lib/api";

export function ChangePasswordForm() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    void getCurrentUser().catch((requestError) => {
      if (requestError instanceof ApiError && requestError.status === 401) {
        window.location.replace("/login");
      } else {
        setError(requestError instanceof Error ? requestError.message : "无法读取登录状态。");
      }
    });
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (newPassword.length < 8) {
      setError("新密码至少需要 8 个字符。");
      return;
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setIsSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      window.location.replace("/");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "密码修改失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleLogout() {
    await logout().catch(() => undefined);
    window.location.replace("/login");
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="password-title">
        <div className="auth-brand"><Sparkles size={18} /><span>天序</span></div>
        <p className="eyebrow">PASSWORD UPDATE</p>
        <h1 id="password-title">设置新密码</h1>
        <p className="auth-intro">验证当前密码后，可以为账户设置新密码。</p>
        <form onSubmit={handleSubmit}>
          {[
            ["当前密码", currentPassword, setCurrentPassword, "current-password"],
            ["新密码", newPassword, setNewPassword, "new-password"],
            ["确认新密码", confirmation, setConfirmation, "new-password"],
          ].map(([label, value, setter, autoComplete]) => (
            <label className="auth-field" key={label as string}>
              <span><LockKeyhole size={15} />{label as string}</span>
              <input
                value={value as string}
                onChange={(event) => (setter as (value: string) => void)(event.target.value)}
                type="password"
                autoComplete={autoComplete as string}
                maxLength={128}
              />
            </label>
          ))}
          {error && <p className="form-message" role="alert"><AlertCircle size={15} />{error}</p>}
          <button className="primary-button auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting && <LoaderCircle className="spin" size={16} />}
            {isSubmitting ? "正在保存" : "保存新密码"}
          </button>
          <button className="auth-link-button" type="button" onClick={() => void handleLogout()}>
            退出当前账户
          </button>
        </form>
      </section>
    </main>
  );
}
