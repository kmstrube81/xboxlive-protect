import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Layout from "./Layout";
import * as authApiModule from "../api/auth";
import * as authHook from "../hooks/useAuth";
import { AUTH_QUERY_KEY } from "../hooks/useAuth";

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderLayout(qc: QueryClient) {
  vi.spyOn(authHook, "useAuth").mockReturnValue({
    user: { username: "admin", must_change_password: false },
    isAuthenticated: true,
    mustChangePassword: false,
    isLoading: false,
  });

  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={["/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/login" element={<div>login page</div>} />
          <Route
            path="/"
            element={<Layout><div>dashboard content</div></Layout>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Layout logout", () => {
  it("calls api.logout, clears only the auth cache, and navigates to /login", async () => {
    const qc = makeQc();
    qc.setQueryData(AUTH_QUERY_KEY, { username: "admin", must_change_password: false });
    qc.setQueryData(["status"], { version: "0.0.1", capture_status: "active" });

    const logoutSpy = vi
      .spyOn(authApiModule.authApi, "logout")
      .mockResolvedValue(undefined);

    renderLayout(qc);

    await userEvent.click(screen.getByRole("button", { name: /log out/i }));

    await waitFor(() =>
      expect(screen.getByText("login page")).toBeInTheDocument(),
    );

    expect(logoutSpy).toHaveBeenCalledOnce();
    // Auth query must be fully removed from the cache.
    expect(qc.getQueryData(AUTH_QUERY_KEY)).toBeUndefined();
    // Other authenticated queries must be untouched — targeted clear only.
    expect(qc.getQueryData(["status"])).toEqual({
      version: "0.0.1",
      capture_status: "active",
    });
  });
});
