import { type FormEvent, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { authApi } from "../api/auth";
import { ApiError } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { AUTH_QUERY_KEY } from "../hooks/useAuth";
import Button from "../components/Button";
import Input from "../components/Input";
import FormError from "../components/FormError";

export default function Login() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { isAuthenticated, mustChangePassword, isLoading } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Redirect already-authenticated users away from /login.
  useEffect(() => {
    if (isLoading) return;
    if (isAuthenticated && mustChangePassword) navigate("/change-password", { replace: true });
    else if (isAuthenticated) navigate("/", { replace: true });
  }, [isAuthenticated, mustChangePassword, isLoading, navigate]);

  const loginMutation = useMutation({
    mutationFn: authApi.login,
    onSuccess: async (me) => {
      await queryClient.invalidateQueries({ queryKey: AUTH_QUERY_KEY });
      if (me.must_change_password) {
        navigate("/change-password", { replace: true });
      } else {
        navigate("/", { replace: true });
      }
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        setErrorMsg(err.message);
      } else {
        setErrorMsg("An unexpected error occurred.");
      }
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setErrorMsg(null);
    loginMutation.mutate({ username, password });
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 dark:bg-slate-900">
      <div className="w-full max-w-sm">
        <h1 className="mb-6 text-center text-2xl font-bold text-slate-800 dark:text-slate-100">
          xboxlive-protect
        </h1>
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <h2 className="mb-4 text-lg font-semibold text-slate-700 dark:text-slate-200">
            Sign in
          </h2>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
            <Input
              label="Username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
            <Input
              label="Password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <FormError message={errorMsg} />
            <Button type="submit" loading={loginMutation.isPending} className="w-full">
              Sign in
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
