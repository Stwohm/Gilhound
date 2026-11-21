import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { components } from "./api/types";
import { api } from "./api/client";

export const route = (world: string) => `/li ${world.toLowerCase()} market`;
type Status = components["schemas"]["BridgeStatus"];
const labels = {
  available: "Available",
  disabled: "disabled in Restocky",
  not_running: "Restocky not running",
  incompatible: "Restocky 0.3.9+ required",
};
const Context = createContext({ home: "", bridge: null as Status | null, notify: (_text: string) => {} });
export function TravelProvider({
  home,
  children,
}: {
  home: string;
  children: ReactNode;
}) {
  const [bridge, setBridge] = useState<Status | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      const status = await api.bridge().catch(() => null);
      if (active) {
        setBridge(status);
        timer = setTimeout(refresh, 5000);
      }
    }
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  const [toast, setToast] = useState({ text: "", revision: 0 });
  const notify = (text: string) => setToast((t) => ({ text, revision: t.revision + 1 }));
  useEffect(() => {
    if (toast.text) {
      const timer = setTimeout(() => setToast((t) => ({ ...t, text: "" })), 3500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  return (
    <Context.Provider value={{ home, bridge, notify }}>
      {children}
      {toast.text && (
        <div role="status" className="travel-toast">
          {toast.text}
        </div>
      )}
    </Context.Provider>
  );
}
export function BridgeIndicator() {
  const { bridge } = useContext(Context);
  return <span>Restocky bridge · {bridge ? labels[bridge.status] : "Status unavailable"}
    {bridge?.plugin_version && ` · v${bridge.plugin_version}`}
    {bridge?.world && ` · ${bridge.world}`}</span>;
}

export function TravelButtons({ world }: { world?: string | null }) {
  const { home, bridge, notify } = useContext(Context);
  const [busy, setBusy] = useState(false);
  const target = world === undefined ? home : world;
  const available = bridge?.status === "available";
  const reason = bridge ? labels[bridge.status] : "Bridge status unavailable";
  if (!target) return null;
  async function act(go: boolean) {
    if (!target) return;
    setBusy(true);
    try {
      let fallback = reason;
      if (go && available) {
        const result = await api.go(target);
        if (result.status !== "unavailable") {
          notify(result.status === "ok" ? "Route sent" : `${result.status}: ${result.reason}`);
          return;
        }
        fallback = result.reason;
      }
      await navigator.clipboard.writeText(route(target));
      notify(go ? `${fallback}. Command copied.` : "Command copied");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Travel action failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="travel-buttons" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        disabled={busy}
        aria-label={`Copy route to ${target}`}
        title="Copy route"
        onClick={() => void act(false)}
      >
        ⧉
      </button>
      <button
        type="button"
        disabled={busy}
        className={available ? undefined : "copy-fallback"}
        aria-label={available ? `Go to ${target}` : `Copy route to ${target}. ${reason}`}
        title={available ? "Go via Restocky" : `${reason}. Click to copy the command.`}
        onClick={() => void act(true)}
      >
        ➤
      </button>
    </span>
  );
}
