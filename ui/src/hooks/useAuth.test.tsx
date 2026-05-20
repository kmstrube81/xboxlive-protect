import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { useAuth } from "./useAuth";
import * as authApi from "../api/auth";
import { ApiError } from "../api/client";

function wrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("useAuth", () => {
  it("returns isLoading=true initially", () => {
    vi.spyOn(authApi.authApi, "getMe").mockReturnValue(new Promise(() => {}));
    const qc = makeQc();
    const { result } = renderHook(() => useAuth(), { wrapper: wrapper(qc) });
    expect(result.current.isLoading).toBe(true);
    expect(result.current.isAuthenticated).toBe(false);
  });

  it("returns authenticated state when /auth/me succeeds", async () => {
    vi.spyOn(authApi.authApi, "getMe").mockResolvedValue({
      username: "admin",
      must_change_password: false,
    });
    const qc = makeQc();
    const { result } = renderHook(() => useAuth(), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.mustChangePassword).toBe(false);
    expect(result.current.user?.username).toBe("admin");
  });

  it("returns mustChangePassword=true when flag is set", async () => {
    vi.spyOn(authApi.authApi, "getMe").mockResolvedValue({
      username: "admin",
      must_change_password: true,
    });
    const qc = makeQc();
    const { result } = renderHook(() => useAuth(), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.mustChangePassword).toBe(true);
  });

  it("returns isAuthenticated=false when /auth/me returns 401", async () => {
    vi.spyOn(authApi.authApi, "getMe").mockRejectedValue(
      new ApiError(401, "Not authenticated", null),
    );
    const qc = makeQc();
    const { result } = renderHook(() => useAuth(), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });
});
