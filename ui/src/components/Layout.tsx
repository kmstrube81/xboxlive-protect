import { type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { authApi, type MeResponse } from "../api/auth";
import { AUTH_QUERY_KEY } from "../hooks/useAuth";
import Button from "./Button";

interface LayoutProps {
  children: ReactNode;
  version?: string;
  captureStatus?: string;
}

export default function Layout({ children, version, captureStatus }: LayoutProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const logoutMutation = useMutation({
    mutationFn: authApi.logout,
    onSettled: () => {
      queryClient.setQueryData<MeResponse | null>(AUTH_QUERY_KEY, null);
      queryClient.removeQueries({ queryKey: AUTH_QUERY_KEY });
      navigate("/login", { replace: true });
    },
  });

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-900">
      {/* Top bar */}
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-2 px-4 py-3 sm:gap-4 sm:px-6 lg:px-8">
          <span className="min-w-0 truncate text-base font-semibold text-slate-800 sm:text-lg dark:text-slate-100">
            xboxlive-protect
          </span>
          <Button
            variant="ghost"
            onClick={() => logoutMutation.mutate()}
            loading={logoutMutation.isPending}
            className="shrink-0 text-sm"
          >
            Log out
          </Button>
        </div>
      </header>

      {/* Page content */}
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>

      {/* Footer */}
      <footer className="border-t border-slate-200 bg-white py-3 text-center text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
        {version ? <span>v{version}</span> : null}
        {version && captureStatus ? <span className="mx-2">·</span> : null}
        {captureStatus ? <span>capture: {captureStatus}</span> : null}
        {(version ?? captureStatus) ? <span className="mx-2">·</span> : null}
        <a
          href="https://github.com/kmstrube81/xboxlive-protect"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-slate-700 dark:hover:text-slate-200"
        >
          GitHub
        </a>
      </footer>
    </div>
  );
}
