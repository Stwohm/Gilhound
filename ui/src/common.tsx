import type { Quote } from "./recheck";
import { TravelButtons } from "./Travel";
import { Children, cloneElement, createContext, Fragment, isValidElement, useContext, useEffect, useRef, useState } from "react";
import type { InputHTMLAttributes, ReactElement, ReactNode } from "react";
import type { components } from "./api/types";
import { api } from "./api/client";
import { mergeColumnOrder, moveColumn } from "./columnOrder";

export type Schema<K extends keyof components["schemas"]> = components["schemas"][K];
export type Settings = Schema<"UserSettings-Output">;
export const defaults: Settings = {
  column_order: {},
  partner_snapshot_hours: 48, partner_cache_min: 45, partner_regular_days: 5, partner_window_days: 14,
  suppress_partner_discord: true, share_sender: "",
  min_net_multiple: 1,
  history_days: 90,
  home_world: "Cerberus",
  buy_region: "Europe",
  scan_interval_min: 5,
  auto_snipe_enabled: true,
  discord_coin_emoji: "🪙",
  dedup_cooldown_h: 6,
  discord_notifier: false,
  travel_cost: { Chaos: 0, Light: 0 },
};

const decimals = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });
const dateTime = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });

export const number = (value: number | null | undefined) => value == null ? "—" : decimals.format(value);
export const gil = (value: number | null | undefined) => value == null ? "—" : integer.format(value);
export const time = (ts: number | null | undefined) => ts == null ? "Unknown" : dateTime.format(ts * 1000);
export const epoch = () => Math.floor(Date.now() / 1000);
export const age = (hours: number | null | undefined) => {
  if (hours == null) return "—";
  if (hours < 1) return `${integer.format(Math.min(59, Math.floor(Math.max(0, hours) * 60)))} min`;
  return `${number(hours)} h`;
};
export const ageTone = (hours: number) => hours < 1 ? "good" : hours < 6 ? "warn" : "muted";
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Something went wrong. Try again.";

export function useResource<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    load().then(
      (result) => { if (active) setData(result); },
      (failure) => { if (active) setError(errorMessage(failure)); },
    );
    return () => { active = false; };
  }, [load, attempt]);

  function retry() {
    setData(undefined);
    setError("");
    setAttempt((value) => value + 1);
  }

  return { data, error, retry, setData };
}

export function PageTitle({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <header className="page-title">
    <div><h1>{title}</h1><p>{description}</p></div>
    {children}
  </header>;
}

export function Section({ title, detail, action, children }: { title: ReactNode; detail?: string; action?: ReactNode; children: ReactNode }) {
  return <section className="panel">
    <div className="section-head">
      <div><h2>{title}</h2>{detail && <p>{detail}</p>}</div>
      {action}
    </div>
    {children}
  </section>;
}

export function Notice({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "warn" | "error" | "good" }) {
  return <div className={`notice ${tone}`} role={tone === "error" ? "alert" : "status"}>{children}</div>;
}

export function Loading({ children }: { children: ReactNode }) {
  return <div className="loading" role="status"><span className="spinner" aria-hidden="true" />{children}</div>;
}

export function ResourceState({ error, retry, loading }: { error: string; retry: () => void; loading: string }) {
  return error ? <Notice tone="error">{error} <button type="button" onClick={retry}>Retry</button></Notice>
    : <Loading>{loading}</Loading>;
}

export function Badge({ children, tone = "muted" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Item({ item, showQuality = true, buyWorld, quote }: { quote?: Quote; buyWorld?: string | null; item: Schema<"ItemRef">; showQuality?: boolean }) {
  const world = item.universalis_url && new URL(item.universalis_url).searchParams.get("server");
  const links = [
    { url: item.universalis_url, label: `Open on Universalis (${world})`, badge: "U" },
    { url: item.saddlebag_url, label: "Open on Saddlebag Exchange", badge: "S" },
  ];
  return <span className="item-name">{item.name}{links.map(({ url, label, badge }) => url &&
    <a key={badge} href={url} target="_blank" rel="noopener noreferrer" title={label} aria-label={label}>
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
        <rect x="1" y="1" width="16" height="16" rx="3" fill="none" stroke="currentColor" />
        <text x="9" y="13" textAnchor="middle" fill="currentColor" fontSize="12">{badge}</text>
      </svg>
    </a>)}<TravelButtons item={item} world={buyWorld} quote={quote} />{showQuality && <Badge tone={item.hq ? "gold" : "muted"}>{item.hq ? "HQ" : "NQ"}</Badge>}</span>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

export function Numeric({ value, onValue, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & {
  value: number;
  onValue: (value: number) => void;
}) {
  return <input type="number" min={0} required {...props} value={Number.isNaN(value) ? "" : value}
    onChange={(event) => onValue(event.currentTarget.valueAsNumber)} />;
}

export type TravelEntry = { dc: string; cost: number };
export const travelEntries = (costs: Record<string, number> = {}): TravelEntry[] =>
  Object.entries(costs).map(([dc, cost]) => ({ dc, cost }));

export function travelCosts(entries: TravelEntry[]) {
  const costs = new Map<string, number>();
  const names = new Set<string>();
  for (const { dc, cost } of entries) {
    const name = dc.trim();
    if (!name || names.has(name.toLowerCase())) throw new Error("Enter a unique name for each data centre.");
    names.add(name.toLowerCase());
    costs.set(name, cost);
  }
  return Object.fromEntries(costs);
}

export function TravelEditor({ entries, onChange }: {
  entries: TravelEntry[];
  onChange: (entries: TravelEntry[]) => void;
}) {
  function update(index: number, entry: TravelEntry) {
    onChange(entries.map((current, i) => i === index ? entry : current));
  }
  return <div className="travel-editor">
    {entries.map((entry, index) => <div className="editor-row" key={index}>
      <Field label="Data centre"><input required value={entry.dc} onChange={(event) => update(index, { ...entry, dc: event.target.value })} /></Field>
      <Field label="Cost (gil)"><Numeric value={entry.cost} onValue={(cost) => update(index, { ...entry, cost })} /></Field>
      <button type="button" onClick={() => onChange(entries.filter((_, i) => i !== index))} aria-label={`Remove travel cost ${index + 1}`}>Remove</button>
    </div>)}
    <button type="button" onClick={() => onChange([...entries, { dc: "", cost: 0 }])}>+ Add data centre</button>
  </div>;
}

type Orders = Record<string, string[]>;
const ColumnOrderContext = createContext<{
  orders: Orders;
  errors: Record<string, string>;
  save: (key: string, order: string[]) => void;
}>({ orders: {}, errors: {}, save: () => {} });

export function ColumnOrderProvider({ children }: { children: ReactNode }) {
  const saved = useResource(api.getColumnOrder);
  const [orders, setOrders] = useState<Orders>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const pending = useRef(new Map<string, Promise<unknown>>());

  function save(key: string, order: string[]) {
    setOrders(previous => ({ ...previous, [key]: order }));
    setErrors(previous => ({ ...previous, [key]: "" }));
    const write = Promise.resolve(pending.current.get(key)).catch(() => undefined)
      .then(() => api.saveColumnOrder(key, order));
    pending.current.set(key, write);
    void write.then(() => finish(""), error => finish(errorMessage(error)));
    function finish(error: string) {
      if (pending.current.get(key) !== write) return;
      pending.current.delete(key);
      setErrors(previous => ({ ...previous, [key]: error }));
    }
  }

  return <ColumnOrderContext.Provider value={{ orders: { ...saved.data, ...orders }, errors, save }}>
    {saved.error && <Notice tone="warn">Could not load column order. <button type="button" onClick={saved.retry}>Retry</button></Notice>}
    {children}
  </ColumnOrderContext.Provider>;
}

type TableElement = ReactElement<{ children?: ReactNode; colSpan?: number; "data-column"?: string }>;

function tableElements(children: ReactNode): TableElement[] {
  return Children.toArray(children).filter(isValidElement) as TableElement[];
}

export function Table({ label, children, className = "", tableKey = label, defaults }: {
  label: string;
  children: ReactNode;
  className?: string;
  tableKey?: string;
  defaults?: readonly string[];
}) {
  const store = useContext(ColumnOrderContext);
  const drag = useRef<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const sections = tableElements(children);
  const header = tableElements(sections.find(section => section.type === "thead")?.props.children)[0];
  const labels = tableElements(header?.props.children).map(cell =>
    cell.props["data-column"] ?? (typeof cell.props.children === "string" ? cell.props.children : null));
  const visible = labels.filter((label): label is string => label !== null);
  const saved = store.orders[tableKey] ?? [];
  const order = mergeColumnOrder(defaults ?? visible, saved);
  const shown = order.filter(column => visible.includes(column));
  // Unlabelled controls stay in place. Full-width rows are left intact.
  let index = 0;
  const positions = labels.map(column => column === null ? null : labels.indexOf(shown[index++]));

  function move(from: string, to: string) {
    const next = moveColumn(order, shown, from, to);
    if (next !== order) store.save(tableKey, next);
    setMenu(null);
  }

  function arrange(section: TableElement, headings: boolean): ReactNode {
    if (section.type === "tr") {
      const cells = tableElements(section.props.children);
      if (cells.length !== labels.length || cells.some(cell => cell.props.colSpan)) return section;
      return cloneElement(section, {}, positions.map((position, slot) => {
        const cell = cells[position ?? slot];
        const column = labels[position ?? slot];
        if (!headings || column === null) return cell;
        const columnIndex = shown.indexOf(column);
        const focusHandle = (element: HTMLElement) =>
          element.closest("th")?.querySelector<HTMLButtonElement>(".column-handle")?.focus();
        const shift = (element: HTMLElement, offset: number) => {
          focusHandle(element);
          move(column, shown[columnIndex + offset]);
        };
        return <th key={column} {...cell.props} scope="col"
          onContextMenu={event => { event.preventDefault(); setMenu(menu === column ? null : column); }}
          onDragOver={event => {
            if (drag.current === null) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onDrop={event => {
            if (drag.current === null) return;
            event.preventDefault();
            move(drag.current, column);
            drag.current = null;
          }}>
          {cell.props.children}
          <button className="column-handle" type="button" title={`Reorder ${column}`}
            aria-label={`Reorder ${column}`} aria-expanded={menu === column} draggable
            onClick={() => setMenu(menu === column ? null : column)}
            onDragStart={event => {
              drag.current = column;
              event.dataTransfer.setData("text/plain", column);
              event.dataTransfer.effectAllowed = "move";
              setMenu(null);
            }}
            onDragEnd={() => { drag.current = null; }}>⠿</button>
          {menu === column && <div className="column-menu" onKeyDown={event => {
            if (event.key === "Escape") {
              focusHandle(event.currentTarget);
              setMenu(null);
            }
          }}>
            <button type="button" disabled={columnIndex === 0} onClick={event => shift(event.currentTarget, -1)}>Move left</button>
            <button type="button" disabled={columnIndex === shown.length - 1} onClick={event => shift(event.currentTarget, 1)}>Move right</button>
          </div>}
        </th>;
      }));
    }
    if (section.type === Fragment || section.type === "thead" || section.type === "tbody" || section.type === "tfoot") {
      return cloneElement(section, {}, tableElements(section.props.children).map(row => arrange(row, headings)));
    }
    return section;
  }

  return <>
    <div className="column-toolbar">
      <button type="button" onClick={() => { store.save(tableKey, []); setMenu(null); }}>Restore default order</button>
      {store.errors[tableKey] && <span role="alert">Column order could not be saved.{" "}
        <button type="button" onClick={() => store.save(tableKey, saved)}>Retry</button>
      </span>}
    </div>
    <div className={`table-scroll ${className}`} tabIndex={0} role="region" aria-label={label}>
      <table aria-label={label}>{sections.map(section => arrange(section, section.type === "thead"))}</table>
    </div>
  </>;
}
