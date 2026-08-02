import { defineStore } from "pinia";
import { api } from "../api/client";
import type { AdminUser, LoginResponse } from "../types/admin";

interface AuthState {
  token: string;
  user: AdminUser | null;
  sessionValidated: boolean;
}

function storedAdminUser(): AdminUser | null {
  const stored = localStorage.getItem("admin_user");
  if (!stored) return null;
  try {
    return JSON.parse(stored) as AdminUser;
  } catch {
    localStorage.removeItem("admin_user");
    return null;
  }
}

export const useAuthStore = defineStore("auth", {
  state: (): AuthState => ({
    token: localStorage.getItem("admin_token") || "",
    user: storedAdminUser(),
    sessionValidated: false,
  }),
  getters: {
    isLoggedIn: (state) => Boolean(state.token),
  },
  actions: {
    async login(username: string, password: string) {
      const { data } = await api.post<LoginResponse>("/admin/auth/login", {
        username,
        password,
      });
      this.token = data.access_token;
      this.user = data.user;
      this.sessionValidated = true;
      localStorage.setItem("admin_token", data.access_token);
      localStorage.setItem("admin_user", JSON.stringify(data.user));
    },
    async loadMe() {
      if (!this.token) return;
      const { data } = await api.get<AdminUser>("/admin/auth/me");
      this.user = data;
      this.sessionValidated = true;
      localStorage.setItem("admin_user", JSON.stringify(data));
    },
    async validateSession() {
      if (!this.token) return false;
      try {
        await this.loadMe();
        return true;
      } catch {
        this.logout();
        return false;
      }
    },
    logout() {
      this.token = "";
      this.user = null;
      this.sessionValidated = false;
      localStorage.removeItem("admin_token");
      localStorage.removeItem("admin_user");
    },
  },
});
