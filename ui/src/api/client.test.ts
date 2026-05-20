import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { client, ApiError } from "./client";

function makeFetch(status: number, body: unknown, ok?: boolean): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok: ok ?? (status >= 200 && status < 300),
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

beforeEach(() => {
  vi.stubGlobal("fetch", undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("client.get", () => {
  it("returns parsed JSON on 200", async () => {
    vi.stubGlobal("fetch", makeFetch(200, { hello: "world" }));
    const result = await client.get<{ hello: string }>("/api/test");
    expect(result).toEqual({ hello: "world" });
  });

  it("throws ApiError with status and message on 401", async () => {
    vi.stubGlobal("fetch", makeFetch(401, { detail: "Not authenticated" }, false));
    await expect(client.get("/api/test")).rejects.toSatisfy(
      (e: unknown) => e instanceof ApiError && e.status === 401 && e.message === "Not authenticated",
    );
  });

  it("throws ApiError on 403 with object detail", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch(403, { detail: { error: "password_change_required", message: "Must change password" } }, false),
    );
    await expect(client.get("/api/test")).rejects.toSatisfy(
      (e: unknown) => e instanceof ApiError && e.status === 403 && e.message === "Must change password",
    );
  });

  it("throws ApiError with generic message on 500 with no detail", async () => {
    vi.stubGlobal("fetch", makeFetch(500, {}, false));
    await expect(client.get("/api/test")).rejects.toSatisfy(
      (e: unknown) => e instanceof ApiError && e.status === 500 && e.message.includes("500"),
    );
  });

  it("throws ApiError with network message when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(client.get("/api/test")).rejects.toSatisfy(
      (e: unknown) => e instanceof ApiError && e.status === 0 && e.message.includes("reach the server"),
    );
  });
});

describe("client.post", () => {
  it("sends JSON body with Content-Type header", async () => {
    const fetchMock = makeFetch(200, { ok: true });
    vi.stubGlobal("fetch", fetchMock);
    await client.post("/api/test", { foo: "bar" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/test",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ foo: "bar" }),
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
  });

  it("returns undefined on 204", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 204 } as Response),
    );
    const result = await client.post("/api/test");
    expect(result).toBeUndefined();
  });
});
