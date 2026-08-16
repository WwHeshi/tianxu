"use client";

import { useEffect, useState } from "react";

import { ApiError, getCurrentUser } from "@/lib/api";
import type { CurrentUser } from "@/lib/types";

export interface AdminGuardState {
  admin: CurrentUser | null;
  isLoading: boolean;
  error: string;
}

export function useAdminGuard(): AdminGuardState {
  const [admin, setAdmin] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    async function verifyAdministrator() {
      try {
        const user = await getCurrentUser();
        if (!active) return;
        if (user.role !== "admin") {
          window.location.replace("/");
          return;
        }
        setAdmin(user);
      } catch (requestError) {
        if (requestError instanceof ApiError && requestError.status === 401) {
          window.location.replace("/login");
          return;
        }
        if (active) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "无法验证管理员身份。",
          );
        }
      } finally {
        if (active) setIsLoading(false);
      }
    }

    void verifyAdministrator();
    return () => {
      active = false;
    };
  }, []);

  return { admin, isLoading, error };
}
