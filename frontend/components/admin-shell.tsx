"use client";

import {
  AlertCircle,
  ArrowLeft,
  LoaderCircle,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { logout } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

interface AdminTopbarProps {
  admin: CurrentUser;
}

export function AdminTopbar({ admin }: AdminTopbarProps) {
  async function handleLogout() {
    await logout().catch(() => undefined);
    window.location.replace("/login");
  }

  return (
    <header className="admin-topbar">
      <Link href="/" className="admin-back"><ArrowLeft size={16} />返回排盘</Link>
      <div><ShieldCheck size={17} /><span>{admin.display_name}</span></div>
      <button type="button" onClick={() => void handleLogout()}><LogOut size={16} />退出</button>
    </header>
  );
}

interface AdminShellProps {
  admin: CurrentUser | null;
  isLoading: boolean;
  loadingText: string;
  error?: string;
  pageClassName?: string;
  layoutClassName?: string;
  children: ReactNode;
  overlay?: ReactNode;
}

export function AdminShell({
  admin,
  isLoading,
  loadingText,
  error = "",
  pageClassName = "",
  layoutClassName = "",
  children,
  overlay,
}: AdminShellProps) {
  if (error) {
    return (
      <main className="auth-state-page">
        <AlertCircle size={24} />
        <section><h1>无法进入管理后台</h1><p>{error}</p></section>
      </main>
    );
  }

  if (isLoading || !admin) {
    return (
      <main className="auth-state-page">
        <LoaderCircle className="spin" size={24} />
        <p>{loadingText}</p>
      </main>
    );
  }

  const pageClasses = ["admin-page", pageClassName].filter(Boolean).join(" ");
  const layoutClasses = ["admin-layout", layoutClassName].filter(Boolean).join(" ");

  return (
    <main className={pageClasses}>
      <AdminTopbar admin={admin} />
      <div className={layoutClasses}>{children}</div>
      {overlay}
    </main>
  );
}
