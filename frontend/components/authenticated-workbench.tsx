"use client";

import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { BaziWorkbench } from "@/components/bazi-workbench";
import { ApiError, getBootstrapStatus, getCurrentUser, logout } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

export function AuthenticatedWorkbench() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void getCurrentUser()
      .then((current) => {
        if (!active) return;
        if (current.must_change_password) {
          window.location.replace("/change-password");
          return;
        }
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
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleLogout() {
    try {
      await logout();
    } finally {
      window.location.replace("/login");
    }
  }

  if (error) {
    return (
      <main className="auth-state-page">
        <section><h1>暂时无法进入系统</h1><p>{error}</p></section>
      </main>
    );
  }
  if (!user) {
    return (
      <main className="auth-state-page" aria-live="polite">
        <LoaderCircle className="spin" size={24} aria-hidden="true" />
        <p>正在验证登录状态</p>
      </main>
    );
  }
  return <BaziWorkbench currentUser={user} onLogout={() => void handleLogout()} />;
}
