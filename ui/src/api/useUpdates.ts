import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "../common";
import { api } from "./client";
import type { components } from "./types";

const FOCUS_CHECK_AGE = 15 * 60;

export function useUpdates() {
  const [info, setInfo] = useState<components["schemas"]["AppInfo"]>();
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const lastAttempt = useRef(0);
  const revision = useRef(0);

  const check = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    lastAttempt.current = Date.now() / 1000;
    revision.current += 1;
    setChecking(true);
    setError("");
    try {
      setInfo(await api.checkUpdate());
    } catch (error) {
      setError(errorMessage(error));
      // The server also records failed feed checks, including their timestamp.
      const status = await api.getInfo().catch(() => undefined);
      if (status) setInfo(status);
    } finally {
      inFlight.current = false;
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    let timer: number;
    async function poll(delay: number) {
      const pollingRevision = revision.current;
      if (!inFlight.current) {
        const status = await api.getInfo().catch(() => undefined);
        if (active && status && pollingRevision === revision.current) {
          setInfo(status);
          if ((status.update_checked_at ?? 0) >= lastAttempt.current) setError("");
        }
      }
      if (active) timer = window.setTimeout(() => void poll(60_000), delay);
    }
    void poll(2_000);
    return () => { active = false; window.clearTimeout(timer); };
  }, []);

  useEffect(() => {
    function onFocus() {
      if (!info?.updates_supported) return;
      const lastCheck = Math.max(info.update_checked_at ?? 0, lastAttempt.current);
      if (Date.now() / 1000 - lastCheck > FOCUS_CHECK_AGE) void check();
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [check, info]);

  let result = error || info?.update_check_error || "";
  if (checking) result = "Checking…";
  else if (!result && info) {
    if (!info.updates_supported) result = "Updates unavailable in dev mode.";
    else if (info.latest_version) result = `v${info.latest_version} available`;
    else if (info.update_checked_at != null) result = `You're up to date (v${info.version})`;
  }
  return { info, checking, check, result };
}
