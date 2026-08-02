import axios from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api/v1";

export const api = axios.create({
  baseURL,
  timeout: 90000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("admin_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("admin_token");
      localStorage.removeItem("admin_user");
      const requestUrl = String(error.config?.url || "");
      const isAdminRequest = requestUrl.includes("/admin");
      const isSessionValidation = requestUrl.includes("/admin/auth/me");
      const isAdminPage = window.location.pathname.startsWith("/admin");
      if (
        !isSessionValidation
        && (isAdminRequest || isAdminPage)
        && window.location.pathname !== "/admin/login"
      ) {
        window.location.href = "/admin/login";
      }
    }
    return Promise.reject(error);
  },
);
