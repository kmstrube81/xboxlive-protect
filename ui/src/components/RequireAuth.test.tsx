import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";
import RequireAuth from "./RequireAuth";
import * as authHook from "../hooks/useAuth";

function renderWithRouter(
  initialPath: string,
  authState: Partial<authHook.AuthState>,
) {
  vi.spyOn(authHook, "useAuth").mockReturnValue({
    user: null,
    isAuthenticated: false,
    mustChangePassword: false,
    isLoading: false,
    ...authState,
  });

  const qc = new QueryClient();

  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }

  render(
    <Wrapper>
      <MemoryRouter
          initialEntries={[initialPath]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
        <Routes>
          <Route path="/login" element={<div>login page</div>} />
          <Route path="/change-password" element={<div>change password page</div>} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<div>dashboard</div>} />
            <Route path="/other" element={<div>other page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </Wrapper>,
  );
}

describe("RequireAuth", () => {
  it("renders children when authenticated and no forced change", () => {
    renderWithRouter("/", { isAuthenticated: true, mustChangePassword: false });
    expect(screen.getByText("dashboard")).toBeInTheDocument();
  });

  it("redirects to /login when not authenticated", () => {
    renderWithRouter("/", { isAuthenticated: false });
    expect(screen.getByText("login page")).toBeInTheDocument();
  });

  it("redirects to /change-password when must_change_password is true and not already there", () => {
    renderWithRouter("/", { isAuthenticated: true, mustChangePassword: true });
    expect(screen.getByText("change password page")).toBeInTheDocument();
  });

  it("renders /change-password when mustChangePassword=true and already on that path", () => {
    renderWithRouter("/change-password", {
      isAuthenticated: true,
      mustChangePassword: true,
    });
    expect(screen.getByText("change password page")).toBeInTheDocument();
  });

  it("shows loading spinner while auth resolves", () => {
    renderWithRouter("/", { isLoading: true });
    // Spinner div — no text content, but the animated element should be present
    expect(document.querySelector(".animate-spin")).toBeInTheDocument();
  });
});
