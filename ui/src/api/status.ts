import { client } from "./client";

export interface RulesCount {
  total: number;
  local: number;
  subscription: number;
}

export interface StatusResponse {
  version: string;
  uptime_seconds: number;
  active_profile: string | null;
  capture_status: "active" | "stale" | "missing";
  capture_last_seen: string | null;
  rules_count: RulesCount;
  blocklist_size: number;
}

export const statusApi = {
  getStatus: () => client.get<StatusResponse>("/api/v1/status"),
};
