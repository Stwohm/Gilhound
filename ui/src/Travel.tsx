import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api/client";

export const route = (world: string) => `/li ${world.toLowerCase()} market`;
const Context = createContext({ home: "", enabled: false, notify: (_text: string) => {} });
export function TravelProvider({
  home,
  enabled,
  children,
}: {
  home: string;
  enabled: boolean;
  children: ReactNode;
}) {
  const [toast, setToast] = useState({ text: "", revision: 0 });
  const notify = (text: string) => setToast((t) => ({ text, revision: t.revision + 1 }));
  useEffect(() => {
    if (toast.text) {
      const timer = setTimeout(() => setToast((t) => ({ ...t, text: "" })), 3500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  return (
    <Context.Provider value={{ home, enabled, notify }}>
      {children}
      {toast.text && (
        <div role="status" className="travel-toast">
          {toast.text}
        </div>
      )}
    </Context.Provider>
  );
}
export function TravelButtons({ world }: { world?: string | null }) {
  const { home, enabled, notify } = useContext(Context);
  const [busy, setBusy] = useState(false);
  const target = world === undefined ? home : world;
  if (!target) return null;
  async function act(go: boolean) {
    if (!target) return;
    setBusy(true);
    try {
      if (go && (await api.go(target))) {
        notify("Route sent");
        return;
      }
      await navigator.clipboard.writeText(route(target));
      notify(go ? "FFXIV is not running" : "Copied");
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
      {enabled && /Windows/i.test(navigator.userAgent) && (
        <button
          type="button"
          disabled={busy}
          aria-label={`Go to ${target}`}
          title="Go sends keystrokes to the game. Make sure chat input is empty and no menu has focus."
          onClick={() => void act(true)}
        >
          ➤
        </button>
      )}
    </span>
  );
}
