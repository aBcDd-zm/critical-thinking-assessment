import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { useAuthStore } from "./auth";

vi.mock("../api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

describe("auth store", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("clears a stale saved session when server validation fails", async () => {
    localStorage.setItem("admin_token", "expired-token");
    localStorage.setItem(
      "admin_user",
      JSON.stringify({ id: 1, username: "admin" }),
    );
    setActivePinia(createPinia());
    vi.mocked(api.get).mockRejectedValueOnce(new Error("401"));

    const auth = useAuthStore();
    await expect(auth.validateSession()).resolves.toBe(false);

    expect(auth.isLoggedIn).toBe(false);
    expect(auth.user).toBeNull();
    expect(auth.sessionValidated).toBe(false);
    expect(localStorage.getItem("admin_token")).toBeNull();
    expect(localStorage.getItem("admin_user")).toBeNull();
  });

  it("marks a valid saved session as verified", async () => {
    localStorage.setItem("admin_token", "valid-token");
    setActivePinia(createPinia());
    vi.mocked(api.get).mockResolvedValueOnce({
      data: { id: 1, username: "admin" },
    });

    const auth = useAuthStore();
    await expect(auth.validateSession()).resolves.toBe(true);

    expect(auth.sessionValidated).toBe(true);
    expect(auth.user?.username).toBe("admin");
  });
});
