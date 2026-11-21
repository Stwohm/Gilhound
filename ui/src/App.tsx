import { useSyncExternalStore } from "react";

const pages = [
  { id: "scan", title: "Scan" },
  { id: "snipes", title: "Snipes" },
  { id: "positions", title: "Positions" },
  { id: "settings", title: "Settings" },
] as const;

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function currentPage() {
  return pages.find((page) => `#${page.id}` === window.location.hash) ?? pages[0];
}

export default function App() {
  const activePage = useSyncExternalStore(subscribe, currentPage);

  return (
    <>
      <header>
        <span className="brand">Gilhound</span>
        <nav aria-label="Main navigation">
          {pages.map((page) => (
            <a
              key={page.id}
              href={`#${page.id}`}
              aria-current={page === activePage ? "page" : undefined}
            >
              {page.title}
            </a>
          ))}
        </nav>
      </header>
      <main aria-labelledby="page-title">
        <h1 id="page-title">{activePage.title}</h1>
      </main>
    </>
  );
}
