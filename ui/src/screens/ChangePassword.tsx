import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { authApi } from "../api/auth";
import { ApiError } from "../api/client";
import { AUTH_QUERY_KEY } from "../hooks/useAuth";
import Button from "../components/Button";
import Input from "../components/Input";
import FormError from "../components/FormError";

export default function ChangePassword() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const changeMutation = useMutation({
    mutationFn: authApi.changePassword,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: AUTH_QUERY_KEY });
      navigate("/", { replace: true });
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
    if (newPassword !== confirm) {
      setErrorMsg("New passwords do not match.");
      return;
    }
    changeMutation.mutate({ old_password: oldPassword, new_password: newPassword });
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 dark:bg-slate-900">
      <div className="w-full max-w-sm">
        <h1 className="mb-6 text-center text-2xl font-bold text-slate-800 dark:text-slate-100">
          xboxlive-protect
        </h1>
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <h2 className="mb-1 text-lg font-semibold text-slate-700 dark:text-slate-200">
            Change password
          </h2>
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
            A new password is required before continuing.
          </p>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
            <Input
              label="Current password"
              type="password"
              autoComplete="current-password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              required
            />
            <Input
              label="New password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
            <Input
              label="Confirm new password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
            <FormError message={errorMsg} />
            <Button type="submit" loading={changeMutation.isPending} className="w-full">
              Change password
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
