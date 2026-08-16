"use client";

import { AuthenticatedPageState } from "@/components/authenticated-page-state";
import { HomeDashboard } from "@/components/home-dashboard";
import { useAuthenticatedUser } from "@/hooks/use-authenticated-user";

export function AuthenticatedHome() {
  const { user, error, onLogout } = useAuthenticatedUser();
  if (!user) return <AuthenticatedPageState error={error} />;
  return <HomeDashboard currentUser={user} onLogout={onLogout} />;
}
