import { client } from "./client";

export interface MeResponse {
  username: string;
  must_change_password: boolean;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface PasswordChangeRequest {
  old_password: string;
  new_password: string;
}

export const authApi = {
  getMe: () => client.get<MeResponse>("/api/v1/auth/me"),
  login: (body: LoginRequest) => client.post<MeResponse>("/api/v1/auth/login", body),
  logout: () => client.post<void>("/api/v1/auth/logout"),
  changePassword: (body: PasswordChangeRequest) =>
    client.post<void>("/api/v1/auth/password", body),
};
