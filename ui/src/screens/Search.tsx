import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Badge, Field, Item, Notice, PageTitle, Section, Table, age, ageTone, errorMessage, gil,
  marketCell, marketColumns, pct, useResource,
} from "../common";
import type { MarketColumn, Schema, Settings } from "../common";
import { route } from "../Travel";

type Entry = Schema<"SearchEntry"> | Schema<"SearchName">;
type ItemRef = Schema<"ItemRef">;
type Resolution = Schema<"SearchResolution">;
type Listing = Schema<"SearchListing">;
type Mode = NonNullable<Schema<"SearchParams">["mode"]>;
type SellRow = Schema<"SearchSellInfo"> & { item: ItemRef };
type Draft = { text: string; hq: boolean; qty: string; itemId?: number };

const sellColumns: MarketColumn<SellRow>[] = [
  marketColumns.item,
  { label: "Sell price", group: "Sales · 7 days", value: row => row.sell_ppu, format: gil },
  marketColumns.unitsPerDay, marketColumns.buyers, marketColumns.sellers, marketColumns.rivalStock,
  marketColumns.buyWorld, marketColumns.buyPrice, marketColumns.profit, marketColumns.roi,
  marketColumns.lastSale, marketColumns.dataAge,
];

const basicRegions = ["Europe", "Chaos", "Light"];
const line = (text = ""): Draft => ({ text, hq: false, qty: "" });
const loadLine = (entry: Schema<"SearchEntry">): Draft => ({
  text: String(entry.item_id), itemId: entry.item_id, hq: entry.hq_only ?? false,
  qty: entry.quantity == null ? "" : String(entry.quantity),
});

function updateLines(previous: Draft[], text: string): Draft[] {
  const used = new Set<number>();
  const values = text.split(/\r?\n/);
  const matches = values.map((value, index) => {
    if (previous[index]?.text === value) {
      used.add(index);
      return previous[index];
    }
  });
  return values.map((value, index) => {
    if (matches[index]) return matches[index];
    const original = previous.findIndex((row, i) => !used.has(i) && row.text === value);
    if (original >= 0) {
      used.add(original);
      return previous[original];
    }
    return undefined;
  }).map((match, index) => {
    if (match) return match;
    const prior = values.length === previous.length && !used.has(index) ? previous[index] : undefined;
    return prior ? { ...prior, text: values[index], itemId: undefined } : line(values[index]);
  });
}

function inputEntries(lines: Draft[]) {
  return lines.flatMap((row, index) => {
    const name = row.text.trim();
    if (!name) return [];
    if (name.length > 200) throw new Error(`Item on line ${index + 1} exceeds 200 characters.`);
    const quantity = row.qty === "" ? null : Number(row.qty);
    if (quantity !== null && (!Number.isSafeInteger(quantity) || quantity < 1)) {
      throw new Error(`Enter a positive whole quantity on line ${index + 1}.`);
    }
    const options = { hq_only: row.hq, quantity };
    const entry: Entry = row.itemId === undefined ? { ...options, name } : { ...options, item_id: row.itemId };
    return [{ index, entry, text: row.text }];
  });
}

function Choices({ items, pick }: { items: ItemRef[]; pick: (item: ItemRef) => void }) {
  return <ul className="search-choices">
    {items.map(item => <li key={item.id}>
      <button type="button" onClick={() => pick(item)}>{item.name} <small>#{item.id}</small></button>
    </li>)}
  </ul>;
}

function Seen({ hours }: { hours?: number | null }) {
  return <Badge tone={hours == null ? "muted" : ageTone(hours)}>{hours == null ? "Unknown" : age(hours)}</Badge>;
}

function Listings({ rows, label, tableKey }: { rows: Listing[]; label: string; tableKey: string }) {
  return <Table label={label} tableKey={tableKey}>
    <thead><tr>{["World", "DC", "Gil per unit", "Units", "Quality", "Total incl. tax", "Last seen"].map(heading =>
      <th key={heading}>{heading}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>
      <td>{row.world}</td><td>{row.dc ?? "—"}</td><td>{gil(row.ppu)}</td><td>{gil(row.qty)}</td>
      <td>{row.hq ? "HQ" : "NQ"}</td><td>{gil(row.total)}</td><td><Seen hours={row.data_age_h} /></td>
    </tr>)}</tbody>
  </Table>;
}

function SellPanel({ row }: { row: SellRow }) {
  return <Table label={`${row.item.name} sell metrics`} tableKey="search-sell">
    <thead><tr>{sellColumns.map(column => <th scope="col" key={column.label}>{column.label}</th>)}</tr></thead>
    <tbody><tr>{sellColumns.map(column => <td key={column.label}>{marketCell(column, row)}</td>)}</tr></tbody>
  </Table>;
}

function SearchResultCard({ row, home, pick }: {
  row: Schema<"SearchRow">; home: string; pick?: (item: ItemRef) => void;
}) {
  const input = "item_id" in row.entry ? String(row.entry.item_id) : row.entry.name;
  const item = row.item;
  const listings = row.listings ?? [];
  const cheapest = listings[0];
  const reference = row.home_cheapest;
  const cover = row.cover;
  const sell = row.sell;
  const listingTable = <Listings rows={listings} label={`${item?.name ?? input} listings`} tableKey="search-listings" />;
  return <Section title={item ? <>
      {item.icon_url && <img className="search-item-icon" src={item.icon_url} width={32} height={32} alt="" />}
      <Item item={{ ...item, hq: cheapest?.hq ?? item.hq }} buyWorld={cheapest?.world ?? null} showQuality={false} />
    </> : input} detail={row.entry.hq_only ? "HQ only" : undefined}>
    {row.error && <Notice tone="warn">{row.error}</Notice>}
    {!!row.candidates?.length && pick && <Choices items={row.candidates} pick={pick} />}
    {row.warnings?.map(warning => <Notice key={warning} tone="warn">{warning}</Notice>)}
    {sell && item && <SellPanel row={{ ...sell, item }} />}
    {item && !sell && <div className="panel-body search-reference">
      <span><strong>{home} lowest</strong> {reference
        ? <>{gil(reference.ppu)} gil/unit · {gil(reference.qty)} {reference.hq ? "HQ" : "NQ"} · {gil(reference.total)} gil incl. tax · <Seen hours={reference.data_age_h} /></>
        : "—"}</span>
      <span><strong>{home} 7-day median</strong> {row.home_median_7d == null ? "—"
        : <>{gil(row.home_median_7d)} gil/unit · Last sale <Seen hours={row.home_sale_age_h} /></>}</span>
    </div>}
    {cheapest ? sell ? <details className="panel-body">
      <summary>Regional listings</summary>
      {listingTable}
    </details> : listingTable
      : item && !row.error && <Notice>No matching listings in this region.</Notice>}
    {cover && <details className="panel-body">
      <summary>{cover.covered < cover.requested
        ? `Only ${gil(cover.covered)} of ${gil(cover.requested)} units available`
        : `${gil(cover.covered)} units in whole stacks for ${gil(cover.requested)} requested`}
        {" · "}{gil(cover.total_cost)} gil incl. travel</summary>
      <p>{gil(cover.purchase_cost)} gil incl. tax · {gil(cover.travel_cost)} gil travel
        {cover.worlds.length > 0 && ` · ${cover.worlds.join(", ")}`}</p>
      {sell?.cover_profit != null && <p>
        Estimated profit {gil(sell.cover_profit)} gil
        {sell.cover_roi_pct != null && ` · ${pct(sell.cover_roi_pct)} ROI`}
      </p>}
      {cover.listings.length > 0 && <Listings rows={cover.listings}
        label={`${item?.name ?? input} purchase plan`} tableKey="search-cover" />}
    </details>}
  </Section>;
}

export default function Search({ active, home, settings }: {
  active: boolean; home: string; settings: ReturnType<typeof useResource<Settings>>;
}) {
  const library = useResource(api.searchLists);
  const [regions, setRegions] = useState<string[]>(basicRegions);
  const [regionError, setRegionError] = useState("");
  const [regionAttempt, setRegionAttempt] = useState(0);
  const [low, setLow] = useState<Schema<"LowStockItems">>();
  const [lines, setLines] = useState([line()]);
  const [activeLine, setActiveLine] = useState(0);
  const [region, setRegion] = useState("Europe");
  const [selected, setSelected] = useState("");
  const [suggestion, setSuggestion] = useState<{ query: string; result: Resolution }>();
  const [issues, setIssues] = useState<Record<number, Resolution>>({});
  const [output, setOutput] = useState<{ result: Schema<"SearchResult">; input: ReturnType<typeof inputEntries> }>();
  const [busy, setBusy] = useState(false);
  const [savingMode, setSavingMode] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total?: number | null }>();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [copyFallback, setCopyFallback] = useState("");
  const mode = settings.data?.search_mode ?? "buy";
  const watch = lines[activeLine];
  const query = watch?.itemId === undefined ? watch?.text.trim() ?? "" : "";

  useEffect(() => {
    if (!active) return;
    let live = true;
    api.searchRegions().then(values => {
      if (live) { setRegions(values); setRegionError(""); }
    }, error => { if (live) setRegionError(errorMessage(error)); });
    api.lowStock().then(data => { if (live) setLow(data); },
      () => { if (live) setLow({ reason: "Restocky low-stock data unavailable." }); });
    return () => { live = false; };
  }, [active, regionAttempt]);

  useEffect(() => {
    if (!active || query.length < 2 || query.length > 200) return;
    let live = true;
    const timer = window.setTimeout(() => {
      api.resolveSearch(query).then(result => {
        if (live) setSuggestion({ query, result });
      }, () => { if (live) setSuggestion(undefined); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [active, query]);

  function update(index: number, patch: Partial<Draft>) {
    setLines(current => current.map((row, i) => i === index ? { ...row, ...patch } : row));
  }

  function pick(index: number, item: ItemRef) {
    update(index, { text: item.name, itemId: item.id });
    setIssues(current => {
      const next = { ...current };
      delete next[index];
      return next;
    });
  }

  function readInput() {
    const input = inputEntries(lines);
    if (!input.length || input.length > 1000) throw new Error("Enter between 1 and 1,000 items.");
    return input;
  }

  async function run() {
    setError("");
    setMessage("");
    setBusy(true);
    setProgress({ done: 0 });
    try {
      const input = readInput();
      const result = await api.search({ entries: input.map(row => row.entry), region, mode, top: 10 },
        (done, total) => setProgress({ done, total }));
      setOutput({ result, input });
      setCopyFallback("");
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setProgress(undefined);
      setBusy(false);
    }
  }

  async function changeMode(next: Mode) {
    if (next === mode) return true;
    setSavingMode(true);
    setError("");
    try {
      settings.setData(await api.saveSettings({ search_mode: next }));
      return true;
    } catch (error) {
      setError(errorMessage(error));
      return false;
    } finally {
      setSavingMode(false);
    }
  }

  async function load() {
    const saved = library.data?.lists?.[selected];
    if (!saved || !await changeMode(saved.mode ?? "buy")) return;
    setLines(saved.entries.map(loadLine));
    setRegion(saved.region ?? "Europe");
    setIssues({});
    setError("");
    setMessage(`Loaded ${selected}.`);
  }

  async function changeList(action: Schema<"SearchListChange">["action"]) {
    const entered = action === "create" || action === "rename"
      ? window.prompt(action === "create" ? "Name for the new search list" : "New list name", action === "rename" ? selected : "")
      : selected;
    if (entered == null) return;
    if (action === "delete" && !window.confirm(`Delete the saved list “${selected}”?`)) return;
    const name = entered.trim();
    const change: Schema<"SearchListChange"> = {
      action, name: action === "create" ? name : selected,
      new_name: action === "rename" ? name : undefined,
    };
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (!name || name.length > 80) throw new Error("Enter a list name from 1 to 80 characters.");
      if (action === "create" || action === "save") {
        const input = readInput();
        const lookups = new Map<string, Promise<Resolution>>();
        const issues: Record<number, Resolution> = {};
        const entries: Schema<"SearchEntry">[] = [];
        for (const { index, entry } of input) {
          if ("item_id" in entry) {
            entries.push(entry);
          } else {
            if (!lookups.has(entry.name)) lookups.set(entry.name, api.resolveSearch(entry.name));
            const resolved = await lookups.get(entry.name)!;
            if (resolved.item) entries.push({ item_id: resolved.item.id, hq_only: entry.hq_only, quantity: entry.quantity });
            else issues[index] = resolved;
          }
        }
        setIssues(issues);
        if (Object.keys(issues).length) throw new Error("Resolve the highlighted items before saving.");
        change.search = { entries, region, mode };
      }
      library.setData(await api.editSearchLists(change));
      setSelected(action === "delete" ? "" : name);
      const verb = { create: "Saved", save: "Saved", rename: "Renamed to", delete: "Deleted" }[action];
      setMessage(`${verb} ${name}.`);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function addLowStock() {
    const existing = new Set(lines.map(row => `${row.itemId ?? row.text.trim()}:${row.hq}`));
    const more = (low?.entries ?? []).filter(row => !existing.has(`${row.item_id}:${Boolean(row.hq_only)}`));
    setLines(current => [...current.filter(row => row.text.trim()), ...more.map(loadLine)]);
    setIssues({});
    setMessage(more.length ? `Added ${more.length} low-stock items.` : "Low-stock items are already in the editor.");
  }

  const stops = [...new Set(output?.result.rows.flatMap(row =>
    row.cover ? row.cover.worlds : row.sell
      ? row.sell.buy_world ? [row.sell.buy_world] : []
      : row.listings?.[0] ? [row.listings[0].world] : []) ?? [])];
  const routeStops = output && stops.length
    ? [...stops.filter(world => world !== output.result.home_world), output.result.home_world] : [];
  const routeText = routeStops.map(route).join("\n");
  async function copyRoute() {
    try {
      await navigator.clipboard.writeText(routeText);
      setCopyFallback("");
      setMessage("Combined route copied.");
    } catch {
      setCopyFallback(routeText);
      setMessage("Select and copy the combined route below.");
    }
  }

  const options = [...new Set([...regions, home, region])];
  const captured = suggestion?.query === query ? suggestion?.result : undefined;
  const suggested = captured?.item ? [captured.item] : captured?.candidates ?? [];
  const hasItems = lines.some(row => row.text.trim());
  return <>
    <PageTitle title="Search" description={mode === "buy"
      ? "Compare listings and plan whole-stack market purchases."
      : "Check demand and resale profit on your home world."} />
    <Section title="Item search">
      <form className="panel-body" onSubmit={event => { event.preventDefault(); void run(); }}>
        <fieldset className="search-form" disabled={busy || savingMode}>
          <div className="form-actions">
            <Field label="Saved list"><select value={selected} onChange={event => setSelected(event.currentTarget.value)}>
              <option value="">Choose a list</option>
              {Object.keys(library.data?.lists ?? {}).sort((a, b) => a.localeCompare(b)).map(name =>
                <option key={name}>{name}</option>)}
            </select></Field>
            <button type="button" disabled={!selected} onClick={() => void load()}>Load</button>
            <button type="button" disabled={!selected || !hasItems} onClick={() => void changeList("save")}>Save</button>
            <button type="button" disabled={!hasItems} onClick={() => void changeList("create")}>Save as</button>
            <button type="button" disabled={!selected} onClick={() => void changeList("rename")}>Rename</button>
            <button type="button" disabled={!selected} onClick={() => void changeList("delete")}>Delete</button>
          </div>
          <div className="form-actions">
            <Field label="Region or world"><select value={region} onChange={event => setRegion(event.currentTarget.value)}>
              {options.map(option => <option key={option}>{option}</option>)}
            </select></Field>
            <div className="search-mode" role="group" aria-label="Search mode">
              {(["buy", "sell"] as const).map(value =>
                <button key={value} type="button" aria-pressed={mode === value}
                  onClick={() => void changeMode(value)}>{value === "buy" ? "Buy" : "Sell"}</button>)}
            </div>
          </div>
          <Field label="Item names or IDs, one per line"><textarea rows={5}
            value={lines.map(row => row.text).join("\n")}
            onSelect={event => setActiveLine(event.currentTarget.value.slice(0, event.currentTarget.selectionStart).split("\n").length - 1)}
            onChange={event => {
              const { value, selectionStart } = event.currentTarget;
              setLines(current => updateLines(current, value));
              setActiveLine(value.slice(0, selectionStart).split("\n").length - 1);
              setIssues({});
            }} /></Field>
          {!!suggested.length && <div aria-label={`Suggestions for line ${activeLine + 1}`}>
            <Choices items={suggested} pick={item => pick(activeLine, item)} />
          </div>}
          {lines.map((row, index) => !!row.text.trim() && <div key={index} className="search-entry">
            <div className="form-actions">
              <strong>{row.text}</strong>
              <label><input type="checkbox" checked={row.hq} aria-label={`HQ only for line ${index + 1}`}
                onChange={event => update(index, { hq: event.currentTarget.checked })} /> HQ only</label>
              <label>Quantity <input type="number" min={1} step={1} value={row.qty}
                aria-label={`Quantity for line ${index + 1}`} placeholder="Any"
                onChange={event => update(index, { qty: event.currentTarget.value })} /></label>
            </div>
            {issues[index]?.error && <Notice tone="warn">{issues[index].error}</Notice>}
            {!!issues[index]?.candidates?.length && <Choices items={issues[index].candidates!} pick={item => pick(index, item)} />}
          </div>)}
          <div className="form-actions">
            <span title={low?.reason ?? (low ? undefined : "Checking Restocky low-stock data…")}>
              <button type="button" disabled={!low?.entries?.length} onClick={addLowStock}>Add low-stock items</button>
            </span>
            <button type="submit" className="primary" disabled={!hasItems}>Run</button>
          </div>
        </fieldset>
      </form>
      {regionError && <Notice tone="warn">Could not load the world list. <button type="button"
        onClick={() => setRegionAttempt(value => value + 1)}>Retry</button></Notice>}
      {library.error && <Notice tone="warn">{library.error} <button type="button" onClick={library.retry}>Retry</button></Notice>}
      {library.data?.warning && <Notice tone="warn">{library.data.warning}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice>{message}{copyFallback && <textarea readOnly value={copyFallback} />}</Notice>}
      {progress && <Notice>Searching {progress.total ? `· ${progress.done}/${progress.total}` : "…"}{" "}
        <progress aria-label="Item search progress" max={progress.total || undefined} value={progress.total ? progress.done : undefined} />
      </Notice>}
    </Section>
    {output && <>
      <Section title={`Results · ${output.result.region}`} action={<button type="button" disabled={!routeText}
        onClick={() => void copyRoute()}>Copy combined route</button>}>
        {!!routeStops.length && <p className="panel-body">{routeStops.join(" → ")}</p>}
      </Section>
      {output.result.rows.map((row, index) => {
        const source = output.input[index];
        const canPick = !busy && source && lines[source.index]?.text === source.text;
        return <SearchResultCard key={index} row={row} home={output.result.home_world}
          pick={canPick ? item => { pick(source.index, item); setMessage("Item selected. Run to refresh results."); } : undefined} />;
      })}
    </>}
  </>;
}
