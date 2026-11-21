import { useEffect, useSyncExternalStore } from "react";
import { api, mockMode } from "./api/client";
import { Badge, defaults, useResource } from "./common";
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
  return pages.find((page) => `#${page.id}` === window.location.hash) ?? pages[0];
}

export default function App() {
  const page = useSyncExternalStore(subscribe, currentPage);
  const settings = useResource(api.getSettings);

  useEffect(() => {
    document.title = `${page.title} · Gilhound`;
    window.scrollTo(0, 0);
  }, [page]);

  const preferences = settings.data ?? defaults;
  const screens = {
    scan: null,
    snipes: <Snipes />,
    retainer: null,
    positions: <Positions />,
    settings: <SettingsScreen resource={settings} />,
  };

  return <div className="app">
    <a href="#main" className="skip-link" onClick={(event) => {
      event.preventDefault();
      document.getElementById("main")?.focus();
    }}>Skip to content</a>
    <aside className="sidebar">
      <a className="brand" href="#scan"><span className="brand-mark" aria-hidden="true"><i /></span><span>Gilhound</span></a>
      <nav aria-label="Main navigation">
        {pages.map((link) => <a key={link.id} href={`#${link.id}`} aria-current={link === page ? "page" : undefined}>
          <span className={`nav-icon ${link.icon}`} aria-hidden="true" />{link.title}
        </a>)}
      </nav>
      <div className="sidebar-foot">
        {mockMode && <Badge tone="gold">Mock data</Badge>}
        <div><span className="home-dot" />{preferences.home_world}<span className="foot-region">{preferences.buy_region}</span></div>
      </div>
    </aside>
    <main id="main" tabIndex={-1} aria-label={page.title}>
      <div className="retained-screen" hidden={page.id !== "scan"}><Scan settings={preferences} /></div>
      <div className="retained-screen" hidden={page.id !== "retainer"}><Retainer settings={preferences} /></div>
      {screens[page.id]}
    </main>
  </div>;
}
