"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, getBootstrapStatus, getCurrentUser, logout } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

export function useAuthenticatedUser() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void getCurrentUser()
      .then((current) => {
        if (active) setUser(current);
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

  const handleLogout = useCallback(async () => {
    try {
      await logout();
    } finally {
      window.location.replace("/login");
    }
  }, []);

  return {
    user,
    error,
    onLogout: () => void handleLogout(),
  };
}
