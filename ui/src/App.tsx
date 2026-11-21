import { useEffect, useState, useSyncExternalStore } from "react";
import brandIcon from "../../assets/icon-256.png";
import { api, mockMode } from "./api/client";
import { useUpdates } from "./api/useUpdates";
import { Badge, Notice, defaults, errorMessage, useResource } from "./common";
import Scan from "./screens/Scan";
import Snipes from "./screens/Snipes";
import Retainer from "./screens/Retainer";
import Positions from "./screens/Positions";
import SettingsScreen from "./screens/Settings";

const pages = [
  { id: "scan", title: "Scan", icon: "scan" },
  { id: "snipes", title: "Snipes", icon: "bolt" },
  { id: "retainer", title: "Retainer", icon: "bag" },
  { id: "positions", title: "Positions", icon: "chart" },
  { id: "settings", title: "Settings", icon: "gear" },
] as const;

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function currentPage() {
  return pages.find((page) => `#${page.id}` === window.location.hash.split("/")[0]) ?? pages[0];
}

function UpdateBanner({ version }: { version?: string | null }) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total?: number | null }>();
  const [message, setMessage] = useState("");

  async function apply() {
    setBusy(true);
    setMessage("");
    setProgress({ done: 0 });
    try {
      await api.applyUpdate((done, total) => setProgress({ done, total }));
      setMessage(mockMode ? "Update simulated." : "Restarting Gilhound…");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  if (!version && !busy) return null;
  return <Notice tone="warn">
    {version && `Update to v${version} available `}
    <button type="button" disabled={busy} onClick={() => void apply()}>Download &amp; restart</button>{" "}
    {busy && <progress aria-label="Update download" max={progress?.total || undefined}
      value={progress?.total ? progress.done : undefined} />}
    <span role="status">{message}</span>
  </Notice>;
}

export default function App() {
  const page = useSyncExternalStore(subscribe, currentPage);
  const settings = useResource(api.getSettings);
  const updates = useUpdates();
  const info = updates.info;

  useEffect(() => {
    document.title = `${page.title} · Gilhound`;
    window.scrollTo(0, 0);
  }, [page]);

  const preferences = settings.data ?? defaults;
  const screens = {
    scan: null,
    snipes: <Snipes resource={settings} />,
    retainer: null,
    positions: <Positions />,
    settings: <SettingsScreen resource={settings} updates={updates} />,
  };

  return <div className="app">
    <a href="#main" className="skip-link" onClick={(event) => {
      event.preventDefault();
      document.getElementById("main")?.focus();
    }}>Skip to content</a>
    <aside className="sidebar">
      <a className="brand" href="#scan">
        <img className="brand-mark" src={brandIcon} alt="" width={33} height={33} />
        <span>Gilhound</span>
      </a>
      <nav aria-label="Main navigation">
        {pages.map((link) => <a key={link.id} href={`#${link.id}`} aria-current={link === page ? "page" : undefined}>
          <span className={`nav-icon ${link.icon}`} aria-hidden="true" />{link.title}
        </a>)}
      </nav>
      <div className="sidebar-foot">
        {mockMode && <Badge tone="gold">Mock data</Badge>}
        <div><span className="home-dot" />{preferences.home_world}<span className="foot-region">{preferences.buy_region}</span></div>
        <div>
          {info && <span>v{info.version}</span>}
          <button className="update-refresh" type="button" title="Check for updates" aria-label="Check for updates"
            disabled={updates.checking} onClick={() => void updates.check()}><span aria-hidden="true">⟳</span></button>
        </div>
        {page.id !== "settings" && <small className="foot-region" role="status">{updates.result}</small>}
      </div>
    </aside>
    <main id="main" tabIndex={-1} aria-label={page.title}>
      <UpdateBanner version={info?.update_available ? info.latest_version : null} />
      <div className="retained-screen" hidden={page.id !== "scan"}><Scan settings={preferences} /></div>
      <div className="retained-screen" hidden={page.id !== "retainer"}><Retainer settings={preferences} /></div>
      {screens[page.id]}
    </main>
  </div>;
}
