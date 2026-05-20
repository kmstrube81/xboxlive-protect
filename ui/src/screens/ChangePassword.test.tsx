import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ChangePassword from "./ChangePassword";
import * as authApiModule from "../api/auth";
import { ApiError } from "../api/client";

function makeQc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderChangePassword(qc: QueryClient) {
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={["/change-password"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/change-password" element={<ChangePassword />} />
          <Route path="/" element={<div>dashboard</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("ChangePassword screen", () => {
  it("renders three password fields", () => {
    renderChangePassword(makeQc());
    expect(screen.getByLabelText(/current password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^new password$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/confirm/i)).toBeInTheDocument();
  });

  it("navigates to / on successful password change", async () => {
    vi.spyOn(authApiModule.authApi, "changePassword").mockResolvedValue(undefined);
    renderChangePassword(makeQc());

    await userEvent.type(screen.getByLabelText(/current password/i), "old");
    await userEvent.type(screen.getByLabelText(/^new password$/i), "newpass");
    await userEvent.type(screen.getByLabelText(/confirm/i), "newpass");
    await userEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() => expect(screen.getByText("dashboard")).toBeInTheDocument());
  });

  it("shows error when new passwords do not match (client-side)", async () => {
    renderChangePassword(makeQc());

    await userEvent.type(screen.getByLabelText(/current password/i), "old");
    await userEvent.type(screen.getByLabelText(/^new password$/i), "newpass");
    await userEvent.type(screen.getByLabelText(/confirm/i), "different");
    await userEvent.click(screen.getByRole("button", { name: /change password/i }));

    expect(screen.getByRole("alert")).toHaveTextContent("do not match");
  });

  it("shows error on 400 wrong current password", async () => {
    vi.spyOn(authApiModule.authApi, "changePassword").mockRejectedValue(
      new ApiError(400, "Current password is incorrect", null),
    );
    renderChangePassword(makeQc());

    await userEvent.type(screen.getByLabelText(/current password/i), "wrong");
    await userEvent.type(screen.getByLabelText(/^new password$/i), "newpass");
    await userEvent.type(screen.getByLabelText(/confirm/i), "newpass");
    await userEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Current password is incorrect"),
    );
  });

  it("shows network error on fetch failure", async () => {
    vi.spyOn(authApiModule.authApi, "changePassword").mockRejectedValue(
      new ApiError(0, "Couldn't reach the server — check your connection.", null),
    );
    renderChangePassword(makeQc());

    await userEvent.type(screen.getByLabelText(/current password/i), "old");
    await userEvent.type(screen.getByLabelText(/^new password$/i), "newpass");
    await userEvent.type(screen.getByLabelText(/confirm/i), "newpass");
    await userEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Couldn't reach the server"),
    );
  });
});
