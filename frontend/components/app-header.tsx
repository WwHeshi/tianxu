"use client";

import {
  CalendarDays,
  FlaskConical,
  LibraryBig,
  LogOut,
  Menu,
  MessageCircle,
  Network,
  Settings,
  Sparkles,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ModelSettingsModal } from "@/components/model-settings-modal";
import type { CurrentUser, ModelSettings } from "@/lib/types";

interface AppHeaderProps {
  currentUser: CurrentUser;
  section: string;
  onLogout: () => void;
  onOpenModelSettings?: () => void;
  modelSettingsOpen?: boolean;
  showNavigation?: boolean;
  wide?: boolean;
}

interface NavigationItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const USER_NAVIGATION: NavigationItem[] = [
  { href: "/chart", label: "八字排盘", icon: CalendarDays },
  { href: "/chat", label: "命理对话", icon: MessageCircle },
];

const ADMIN_NAVIGATION: NavigationItem[] = [
  { href: "/admin/knowledge", label: "知识库", icon: LibraryBig },
  { href: "/admin/graph", label: "规则图谱", icon: Network },
  { href: "/admin/evaluations", label: "评测中心", icon: FlaskConical },
  { href: "/admin/users", label: "用户管理", icon: Users },
];

function isNavigationActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + "/");
}

export function AppHeader({
  currentUser,
  section,
  onLogout,
  onOpenModelSettings,
  modelSettingsOpen,
  showNavigation = true,
  wide = false,
}: AppHeaderProps) {
  const pathname = usePathname();
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [modelSettings, setModelSettings] = useState<ModelSettings | null>(null);
  const navigationItems = currentUser.role === "admin"
    ? [...USER_NAVIGATION, ...ADMIN_NAVIGATION]
    : USER_NAVIGATION;

  const handleSettingsChange = useCallback((settings: ModelSettings) => {
    setModelSettings(settings);
  }, []);

  useEffect(() => {
    setIsMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!isMenuOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setIsMenuOpen(false);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isMenuOpen]);

  function handleOpenSettings() {
    setIsMenuOpen(false);
    if (onOpenModelSettings) {
      onOpenModelSettings();
      return;
    }
    setIsSettingsOpen(true);
  }

  return (
    <>
      <header className={"app-header" + (wide ? " is-wide" : "")}>
        <Link href="/" className="brand-lockup app-header-brand" aria-label="返回天序首页">
          <span className="brand-mark" aria-hidden="true">
            <Sparkles size={17} strokeWidth={1.8} />
          </span>
          <span className="brand-name">天序</span>
          <span className="brand-divider" aria-hidden="true" />
          <span className="brand-section">{section}</span>
        </Link>
        <div className="topbar-actions app-header-actions">
          {showNavigation && (
            <>
              <nav className="app-navigation" data-open={isMenuOpen} aria-label="全局功能导航">
                {navigationItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = isNavigationActive(pathname, item.href);
                  return (
                    <Link
                      className={"settings-button app-navigation-link" + (isActive ? " is-active" : "")}
                      href={item.href}
                      aria-current={isActive ? "page" : undefined}
                      title={item.label}
                      key={item.href}
                      onClick={() => setIsMenuOpen(false)}
                    >
                      <Icon size={16} aria-hidden="true" />
                      <span>{item.label}</span>
                    </Link>
                  );
                })}
                {currentUser.role === "admin" && (
                  <button
                    className={
                      "settings-button app-navigation-link"
                      + ((modelSettingsOpen ?? isSettingsOpen) ? " is-active" : "")
                    }
                    type="button"
                    onClick={handleOpenSettings}
                    aria-label="打开模型 API 设置"
                    title="设置 API"
                  >
                    <Settings size={16} aria-hidden="true" />
                    <span>设置 API</span>
                  </button>
                )}
              </nav>
              <button
                className="app-navigation-menu"
                type="button"
                onClick={() => setIsMenuOpen((open) => !open)}
                aria-expanded={isMenuOpen}
                aria-label={isMenuOpen ? "关闭功能菜单" : "打开功能菜单"}
              >
                {isMenuOpen ? <X size={17} /> : <Menu size={17} />}
              </button>
            </>
          )}
          <span className="current-user" title={currentUser.username}>
            {currentUser.display_name}
            <small>{currentUser.role === "admin" ? "管理员" : "用户"}</small>
          </span>
          <button className="logout-button" type="button" onClick={onLogout} aria-label="退出登录">
            <LogOut size={16} aria-hidden="true" />
          </button>
        </div>
      </header>
      {currentUser.role === "admin" && isSettingsOpen && !onOpenModelSettings && (
        <ModelSettingsModal
          current={modelSettings}
          onClose={() => setIsSettingsOpen(false)}
          onChange={handleSettingsChange}
        />
      )}
    </>
  );
}
