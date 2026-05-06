import type { SettingsPayload, SettingsUpdate } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...(init ?? {}), credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return (await res.json()) as T;
}

export async function fetchSettings(base = ""): Promise<SettingsPayload> {
  return request<SettingsPayload>(`${base}/api/settings`);
}

export async function updateSettings(update: SettingsUpdate, base = ""): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  if (update.model !== undefined) query.set("model", update.model);
  if (update.provider !== undefined) query.set("provider", update.provider);
  return request<SettingsPayload>(`${base}/api/settings/update?${query}`, { method: "PUT" });
}
