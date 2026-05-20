import { useQuery } from "@tanstack/react-query";
import { authApi, type MeResponse } from "../api/auth";
import { ApiError } from "../api/client";

export interface AuthState {
  user: MeResponse | null;
  isAuthenticated: boolean;
  mustChangePassword: boolean;
  isLoading: boolean;
}

export const AUTH_QUERY_KEY = ["auth", "me"] as const;

export function useAuth(): AuthState {
  const { data, isLoading } = useQuery<MeResponse, ApiError>({
    queryKey: AUTH_QUERY_KEY,
    queryFn: authApi.getMe,
    // 401 means not authenticated — not an error worth retrying.
    retry: (count, err) => (err instanceof ApiError && err.status === 401 ? false : count < 1),
    staleTime: 5_000,
    gcTime: Infinity,
  });

  return {
    user: data ?? null,
    isAuthenticated: data !== undefined,
    mustChangePassword: data?.must_change_password ?? false,
    isLoading,
  };
}
