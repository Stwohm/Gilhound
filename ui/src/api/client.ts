import { mockApi } from "./mock";
import type { operations } from "./types";

type JsonResult<K extends keyof operations> = operations[K]["responses"][200] extends {
  content: { "application/json": infer Data };
} ? Data : never;

type JsonBody<K extends keyof operations> = operations[K] extends {
  requestBody: { content: { "application/json": infer Data } };
} ? Data : never;

export interface Api {
  scan(params: JsonBody<"run_scan_api_scan_post">): Promise<JsonResult<"run_scan_api_scan_post">>;
  retainerScan(params: JsonBody<"run_retainer_scan_api_retainer_scan_post">): Promise<JsonResult<"run_retainer_scan_api_retainer_scan_post">>;
  getSnipes(): Promise<JsonResult<"get_snipes_api_snipes_get">>;
  runSnipes(): Promise<JsonResult<"run_snipes_api_snipes_run_post">>;
  getSources(): Promise<JsonResult<"get_sources_api_sources_get">>;
  getPositions(): Promise<JsonResult<"get_positions_api_positions_get">>;
  getPnl(): Promise<JsonResult<"get_pnl_api_pnl_get">>;
  getSettings(): Promise<JsonResult<"get_settings_api_settings_get">>;
  saveSettings(settings: JsonBody<"put_settings_api_settings_put">): Promise<JsonResult<"put_settings_api_settings_put">>;
  getInfo(): Promise<JsonResult<"get_info_api_info_get">>;
  exportDiagnostics(): Promise<Blob>;
  testDiscord(): Promise<void>;
}

declare global {
  interface Window {
    __GILHOUND__?: { token?: string };
  }
}

const urlToken = new URLSearchParams(window.location.search).get("token");

async function request(path: string, method = "GET", body?: unknown): Promise<Response> {
  const headers = new Headers();
  const token = window.__GILHOUND__?.token || urlToken;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (body !== undefined) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      redirect: "error",
    });
  } catch {
    throw new Error("Could not reach the Gilhound API. Check that the app is running.");
  }

  if (response.status === 501) throw new Error("This feature is not implemented yet.");
  if (response.status === 401) throw new Error("Your session could not be authenticated. Reopen Gilhound.");
  if (response.status === 422) throw new Error("The API rejected one or more fields. Check your input.");
  if (!response.ok) throw new Error(`Gilhound API request failed (HTTP ${response.status}).`);
  return response;
}

async function json<T>(path: string, method?: string, body?: unknown): Promise<T> {
  const response = await request(path, method, body);
  try {
    return await response.json() as T;
  } catch {
    throw new Error("The Gilhound API returned an invalid response.");
  }
}

const realApi: Api = {
  scan: (params) => json("/api/scan", "POST", params),
  retainerScan: (params) => json("/api/retainer-scan", "POST", params),
  getSnipes: () => json("/api/snipes"),
  runSnipes: () => json("/api/snipes/run", "POST"),
  getSources: () => json("/api/sources"),
  getPositions: () => json("/api/positions"),
  getPnl: () => json("/api/pnl"),
  getSettings: () => json("/api/settings"),
  saveSettings: (settings) => json("/api/settings", "PUT", settings),
  getInfo: () => json("/api/info"),
  exportDiagnostics: async () => (await request("/api/diagnostics/export", "POST")).blob(),
  testDiscord: async () => {
    throw new Error("Discord testing is not implemented yet.");
  },
};

const mode = import.meta.env.VITE_API_MODE;
export const mockMode = mode === "mock" || (mode !== "real" && import.meta.env.DEV);
export const api = mockMode ? mockApi : realApi;
