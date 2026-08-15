"use client";

import {
  AlertCircle,
  CheckCircle2,
  KeyRound,
  LoaderCircle,
  Plus,
  RefreshCcw,
  UserRoundCog,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { AdminShell } from "@/components/admin-shell";
import { useAdminGuard } from "@/hooks/use-admin-guard";
import {
  createUser,
  listUsers,
  resetUserPassword,
  revokeUserSessions,
  updateUser,
} from "@/lib/api";
import type { CurrentUser, UserRole } from "@/lib/types";

function formatDate(value: string | null): string {
  if (!value) return "尚未登录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function AdminUsers() {
  const { admin, isLoading: isGuardLoading, error: guardError } = useAdminGuard();
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [busyUserId, setBusyUserId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [role, setRole] = useState<UserRole>("user");
  const [isCreating, setIsCreating] = useState(false);

  const load = useCallback(async () => {
    if (!admin) return;
    setError("");
    try {
      const response = await listUsers();
      setUsers(response.items);
      setTotal(response.total);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法读取用户列表。");
    } finally {
      setIsLoading(false);
    }
  }, [admin]);

  useEffect(() => {
    if (admin) void load();
  }, [admin, load]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (username.trim().length < 3 || !displayName.trim() || temporaryPassword.length < 8) {
      setError("请完整填写用户名、显示名称和至少 8 个字符的临时密码。");
      return;
    }
    setIsCreating(true);
    try {
      await createUser({
        username: username.trim(),
        display_name: displayName.trim(),
        temporary_password: temporaryPassword,
        role,
      });
      setUsername("");
      setDisplayName("");
      setTemporaryPassword("");
      setRole("user");
      setNotice("用户已创建，首次登录时必须修改临时密码。");
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "用户创建失败。");
    } finally {
      setIsCreating(false);
    }
  }

  async function mutateUser(userId: string, action: () => Promise<unknown>, message: string) {
    setError("");
    setNotice("");
    setBusyUserId(userId);
    try {
      await action();
      setNotice(message);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "用户操作失败。");
    } finally {
      setBusyUserId("");
    }
  }

  function handleReset(user: CurrentUser) {
    const password = window.prompt(`为 ${user.display_name} 设置新密码（至少 8 个字符）：`);
    if (password === null) return;
    if (password.length < 8) {
      setError("新密码至少需要 8 个字符。");
      return;
    }
    void mutateUser(
      user.id,
      () => resetUserPassword(user.id, password),
      "永久密码已重置，该用户的现有登录状态已全部失效。",
    );
  }

  return (
    <AdminShell
      admin={admin}
      isLoading={isGuardLoading || isLoading}
      loadingText="正在读取用户"
      error={guardError}
    >
        <section className="admin-heading">
          <div><p className="eyebrow">ACCESS CONTROL</p><h1>用户管理</h1></div>
          <span>共 {total} 个账户</span>
        </section>

        <section className="admin-create-card" aria-labelledby="create-user-title">
          <div><UserRoundCog size={20} /><h2 id="create-user-title">创建用户</h2></div>
          <form onSubmit={handleCreate}>
            <label><span>用户名</span><input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" placeholder="例如 reader01" /></label>
            <label><span>显示名称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="off" placeholder="用户姓名或称呼" /></label>
            <label><span>临时密码</span><input value={temporaryPassword} onChange={(event) => setTemporaryPassword(event.target.value)} type="password" autoComplete="new-password" placeholder="至少 8 个字符" /></label>
            <label><span>角色</span><select value={role} onChange={(event) => setRole(event.target.value as UserRole)}><option value="user">普通用户</option><option value="admin">管理员</option></select></label>
            <button type="submit" disabled={isCreating}>{isCreating ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}{isCreating ? "正在创建" : "创建用户"}</button>
          </form>
        </section>

        {error && <p className="admin-message is-error" role="alert"><AlertCircle size={16} />{error}</p>}
        {notice && <p className="admin-message is-success" role="status"><CheckCircle2 size={16} />{notice}</p>}

        <section className="admin-table-card" aria-label="用户列表">
          <div className="admin-table-header"><strong>账户列表</strong><button type="button" onClick={() => void load()}><RefreshCcw size={14} />刷新</button></div>
          <div className="admin-table-wrap">
            <table>
              <thead><tr><th>用户</th><th>角色</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead>
              <tbody>
                {users.map((user) => {
                  const busy = busyUserId === user.id;
                  return (
                    <tr key={user.id}>
                      <td><strong>{user.display_name}</strong><span>{user.username}{user.id === admin?.id ? " · 当前账户" : ""}</span></td>
                      <td>
                        <select
                          value={user.role}
                          disabled={busy}
                          onChange={(event) => void mutateUser(user.id, () => updateUser(user.id, { role: event.target.value as UserRole }), "用户角色已更新。")}
                        >
                          <option value="user">普通用户</option><option value="admin">管理员</option>
                        </select>
                      </td>
                      <td><span className={`user-status is-${user.status}`}>{user.status === "active" ? "启用" : "停用"}</span>{user.must_change_password && <small>待改密码</small>}</td>
                      <td>{formatDate(user.last_login_at)}</td>
                      <td>
                        <div className="user-actions">
                          <button type="button" disabled={busy} onClick={() => handleReset(user)}><KeyRound size={13} />重置密码</button>
                          <button type="button" disabled={busy} onClick={() => void mutateUser(user.id, () => revokeUserSessions(user.id), "该用户的登录状态已全部撤销。")}>强制退出</button>
                          <button
                            type="button"
                            disabled={busy}
                            data-danger={user.status === "active" || undefined}
                            onClick={() => void mutateUser(user.id, () => updateUser(user.id, { status: user.status === "active" ? "disabled" : "active" }), user.status === "active" ? "用户已停用。" : "用户已启用。")}
                          >
                            {user.status === "active" ? "停用" : "启用"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
    </AdminShell>
  );
}
