"use client";

import {
  AlertCircle,
  LoaderCircle,
} from "lucide-react";
import type { ReactNode } from "react";

import { AppHeader } from "@/components/app-header";
import { logout } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

interface AdminTopbarProps {
  admin: CurrentUser;
  sectionTitle: string;
}

export function AdminTopbar({ admin, sectionTitle }: AdminTopbarProps) {
  async function handleLogout() {
    await logout().catch(() => undefined);
    window.location.replace("/login");
  }

  return (
    <AppHeader
      currentUser={admin}
      section={sectionTitle}
      onLogout={() => void handleLogout()}
    />
  );
}

interface AdminShellProps {
  admin: CurrentUser | null;
  isLoading: boolean;
  loadingText: string;
  sectionTitle: string;
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
  sectionTitle,
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
      <AdminTopbar admin={admin} sectionTitle={sectionTitle} />
      <div className={layoutClasses}>{children}</div>
      {overlay}
    </main>
  );
}
