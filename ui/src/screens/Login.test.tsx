import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";
import Login from "./Login";
import * as authApiModule from "../api/auth";
import * as authHook from "../hooks/useAuth";
import { ApiError } from "../api/client";

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderLogin(
  qc: QueryClient,
  authState: Partial<authHook.AuthState> = {},
) {
  // By default: not authenticated, not loading — user sees the form.
  vi.spyOn(authHook, "useAuth").mockReturnValue({
    user: null,
    isAuthenticated: false,
    mustChangePassword: false,
    isLoading: false,
    ...authState,
  });

  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={["/login"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<div>dashboard</div>} />
          <Route path="/change-password" element={<div>change password</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Login screen", () => {
  it("renders username and password fields", () => {
    renderLogin(makeQc());
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("navigates to / on successful login without forced change", async () => {
    const spy = vi.spyOn(authApiModule.authApi, "login").mockResolvedValue({
      username: "admin",
      must_change_password: false,
    });
    renderLogin(makeQc());

    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "secret");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(screen.getByText("dashboard")).toBeInTheDocument());
    expect(spy.mock.calls[0]?.[0]).toEqual({ username: "admin", password: "secret" });
  });

  it("navigates to /change-password when must_change_password=true after login", async () => {
    vi.spyOn(authApiModule.authApi, "login").mockResolvedValue({
      username: "admin",
      must_change_password: true,
    });
    renderLogin(makeQc());

    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "xboxlive-protect");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() =>
      expect(screen.getByText("change password")).toBeInTheDocument(),
    );
  });

  it("shows error message on 401 bad credentials", async () => {
    vi.spyOn(authApiModule.authApi, "login").mockRejectedValue(
      new ApiError(401, "Invalid credentials", null),
    );
    renderLogin(makeQc());

    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Invalid credentials"),
    );
  });

  it("shows network error message on fetch failure", async () => {
    vi.spyOn(authApiModule.authApi, "login").mockRejectedValue(
      new ApiError(0, "Couldn't reach the server — check your connection.", null),
    );
    renderLogin(makeQc());

    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "pw");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Couldn't reach the server"),
    );
  });

  it("redirects to / when already authenticated without forced change", async () => {
    renderLogin(makeQc(), { isAuthenticated: true, mustChangePassword: false });
    await waitFor(() => expect(screen.getByText("dashboard")).toBeInTheDocument());
  });

  it("redirects to /change-password when authenticated with forced change", async () => {
    renderLogin(makeQc(), { isAuthenticated: true, mustChangePassword: true });
    await waitFor(() =>
      expect(screen.getByText("change password")).toBeInTheDocument(),
    );
  });
});
