import { useQuery } from "@tanstack/react-query";
import { statusApi } from "../api/status";
import { ApiError } from "../api/client";
import Layout from "../components/Layout";

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["status"],
    queryFn: statusApi.getStatus,
    refetchInterval: 10_000,
  });

  const captureLabel =
    data?.capture_status === "active"
      ? "active"
      : data?.capture_status === "stale"
        ? "stale"
        : data?.capture_status === "missing"
          ? "not running"
          : undefined;

  return (
    <Layout version={data?.version} captureStatus={captureLabel}>
      <div className="flex flex-col gap-4">
        <h1 className="text-xl font-semibold text-slate-800 dark:text-slate-100">
          Dashboard
        </h1>

        {isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">Loading status…</p>
        ) : error ? (
          <p className="text-sm text-red-600 dark:text-red-400">
            {error instanceof ApiError ? error.message : "Could not load status."}
          </p>
        ) : data ? (
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-slate-500 dark:text-slate-400">Version</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">
                  {data.version}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">Capture</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">
                  {captureLabel}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">Active profile</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">
                  {data.active_profile ?? "—"}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">Block rules</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">
                  {data.rules_count.total}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">Uptime</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-100">
                  {formatUptime(data.uptime_seconds)}
                </dd>
              </div>
            </dl>
          </div>
        ) : null}

        <p className="text-sm text-slate-500 dark:text-slate-400">
          Stage 5 will add the Live peer table, Rules management, and History screens.
        </p>
      </div>
    </Layout>
  );
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
