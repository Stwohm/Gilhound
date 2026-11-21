import { mockApi } from "./mock";
import type { components, operations } from "./types";

type JsonResult<K extends keyof operations> = operations[K]["responses"] extends { 200: {
  content: { "application/json": infer Data };
} } ? Data : never;

type JsonBody<K extends keyof operations> = operations[K] extends {
  requestBody: { content: { "application/json": infer Data } };
} ? Data : never;

export type Library = "scan" | "snipes";
type Schema = components["schemas"];
export interface Api {
  getProfiles(library: Library): Promise<Schema["ProfileList"]>;
  changeProfile(library: Library, change: Schema["ProfileChange"]): Promise<Schema["ProfileList"]>;
  getHidden(): Promise<Schema["ItemRef"][]>;
  setPersonal(change: Schema["PositionPersonal"]): Promise<void>;
  bridge(): Promise<Schema["BridgeStatus"]>;
  go(world: string): Promise<Schema["BridgeResult"]>;
  history(query: URLSearchParams): Promise<Schema["ArchiveSummary"][]>;
  entry(id: number): Promise<Schema["ArchiveEntry"]>;
  deleteEntry(id: number): Promise<void>;
  scan(params: JsonBody<"run_scan_api_scan_post">, profile?: string): Promise<JsonResult<"run_scan_api_scan_post">>;
  retainerProfiles(): Promise<Record<string, Schema["RetainerScanParams"]>>;
  saveRetainerProfile(name: string, params: Schema["RetainerScanParams"]): Promise<void>;
  retainerScan(params: JsonBody<"run_retainer_scan_api_retainer_scan_post">, profile?: string): Promise<JsonResult<"run_retainer_scan_api_retainer_scan_post">>;
  getSnipeStatus(): Promise<JsonResult<"get_snipe_status_api_snipes_status_get">>;
  getSnipes(): Promise<JsonResult<"get_snipes_api_snipes_get">>;
  runSnipes(): Promise<JsonResult<"run_snipes_api_snipes_run_post">>;
  getSources(): Promise<JsonResult<"get_sources_api_sources_get">>;
  setCost(cost: JsonBody<"set_position_cost_api_positions_cost_put">): Promise<void>;
  getPositions(): Promise<JsonResult<"get_positions_api_positions_get">>;
  getPnl(): Promise<JsonResult<"get_pnl_api_pnl_get">>;
  getSettings(): Promise<JsonResult<"get_settings_api_settings_get">>;
  saveSettings(settings: JsonBody<"put_settings_api_settings_put">): Promise<JsonResult<"put_settings_api_settings_put">>;
  getInfo(): Promise<JsonResult<"get_info_api_info_get">>;
  checkUpdate(): Promise<JsonResult<"check_update_api_update_check_post">>;
  applyUpdate(progress: (done: number, total?: number | null) => void): Promise<void>;
  exportDiagnostics(): Promise<Blob>;
  testDiscord(): Promise<void>;
}

declare global {
  interface Window {
    __GILHOUND__?: { token?: string };
  }
}



async function request(path: string, method = "GET", body?: unknown): Promise<Response> {
  const headers = new Headers();
  const token = window.__GILHOUND__?.token;
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


  if (response.status === 401) throw new Error("Your session could not be authenticated. Reopen Gilhound.");
  if (response.status === 422) throw new Error("The API rejected one or more fields. Check your input.");
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail
      : `Gilhound API request failed (HTTP ${response.status}).`);
  }
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

async function waitForJob(path: string, params?: unknown, progress?: (done: number, total?: number | null) => void) {
  type Job = components["schemas"]["Job"];
  let current = await json<Job>(path, "POST", params);
  while (current.state === "queued" || current.state === "running") {
    progress?.(current.done, current.total);
    await new Promise(resolve => window.setTimeout(resolve, 400));
    current = await json<Job>(`/api/jobs/${current.id}`);
  }
  return current;
}

async function job<T>(path: string, params?: unknown): Promise<T> {
  const current = await waitForJob(path, params);
  if (current.state !== "complete" || current.result == null) throw new Error("Market scan failed. Please retry.");
  return current.result as T;
}

const realApi: Api = {
  getProfiles: library => json("/api/profiles/" + library),
  changeProfile: (library, change) => json("/api/profiles/" + library, "POST", change),
  getHidden: () => json("/api/positions/hidden"),
  setPersonal: async change => { await request("/api/positions/personal", "PUT", change); },
  bridge: () => json("/api/bridge"),
  go: world => json("/api/bridge/go", "POST", { world }),
  history: query => json("/api/history?" + query),
  entry: id => json("/api/history/" + id),
  deleteEntry: async id => { await request("/api/history/" + id, "DELETE"); },
  scan: (params, profile) => job("/api/jobs/scan" + (profile ? "?profile=" + encodeURIComponent(profile) : ""), params),
  retainerProfiles: () => json("/api/retainer-profiles"),
  saveRetainerProfile: async (name, params) => { await request("/api/retainer-profiles?name=" + encodeURIComponent(name), "POST", params); },
  retainerScan: (params, profile) => job("/api/jobs/retainer" + (profile ? "?profile=" + encodeURIComponent(profile) : ""), params),
  getSnipeStatus: () => json("/api/snipes/status"),
  getSnipes: () => json("/api/snipes"),
  runSnipes: () => job("/api/jobs/snipes"),
  getSources: () => json("/api/sources"),
  getPositions: () => json("/api/positions?view=all"),
  setCost: async (cost) => { await request("/api/positions/cost", "PUT", cost); },
  getPnl: () => json("/api/pnl"),
  getSettings: () => json("/api/settings"),
  saveSettings: (settings) => json("/api/settings", "PUT", settings),
  getInfo: () => json("/api/info"),
  checkUpdate: () => json("/api/update/check", "POST"),
  async applyUpdate(progress) {
    const current = await waitForJob("/api/update/apply", undefined, progress);
    if (current.state !== "complete") throw new Error("Update failed. Please retry.");
  },
  exportDiagnostics: async () => (await request("/api/diagnostics/export", "POST")).blob(),
  testDiscord: async () => {
    await request("/api/settings/discord/test", "POST");
  },
};

const mode = import.meta.env.VITE_API_MODE;
export const mockMode = import.meta.env.DEV && mode !== "real";
export const api = mockMode ? mockApi : realApi;
