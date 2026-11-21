import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { api } from "../api/client";
import {
  Badge, Field, Item, Loading, Notice, Numeric, PageTitle, Section, Table, TravelEditor,
  age, ageTone, errorMessage, gil, number, time, travelCosts, travelEntries,
} from "../common";
import type { Schema, Settings } from "../common";

type Params = Schema<"ScanParams">;
type Preset = Schema<"ScanPreset">;
type Row = Schema<"ScanRow">;
type Column = { label: string; sort: (row: Row) => string | number | null; render?: (row: Row) => ReactNode };

const presets: { id: Preset; name: string; detail: string }[] = [
  { id: "flip", name: "Flip", detail: "Balanced profit and sales" },
  { id: "high_margin", name: "High margin", detail: "Higher profit per unit" },
  { id: "volume", name: "Volume", detail: "Faster-selling items" },
  { id: "snipe", name: "Snipe", detail: "Deep listing discounts" },
];

const columns: Column[] = [
  { label: "Item", sort: (r) => r.item.name, render: (r) => <Item item={r.item} showQuality={false} /> },
  { label: "Quality", sort: (r) => +r.item.hq, render: (r) => <Badge tone={r.item.hq ? "gold" : "muted"}>{r.item.hq ? "HQ" : "NQ"}</Badge> },
  { label: "Buy world / DC", sort: (r) => r.buy_world, render: (r) => <>{r.buy_world} <small>{r.buy_dc}</small></> },
  { label: "Buy price", sort: (r) => r.buy_ppu, render: (r) => gil(r.buy_ppu) },
  { label: "Qty at price", sort: (r) => r.buy_qty_at_price },
  { label: "Home median", sort: (r) => r.home_median_7d, render: (r) => gil(r.home_median_7d) },
  { label: "Home lowest", sort: (r) => r.home_lowest, render: (r) => gil(r.home_lowest) },
  { label: "Sellable qty", sort: (r) => r.sellable_qty },
  { label: "Profit / unit", sort: (r) => r.profit_per_unit, render: (r) => <strong className={r.profit_per_unit >= 0 ? "profit" : "loss"}>{gil(r.profit_per_unit)}</strong> },
  { label: "Capped total", sort: (r) => r.total_profit_capped, render: (r) => gil(r.total_profit_capped) },
  { label: "Wait (days)", sort: (r) => r.est_wait_days },
  { label: "Units / day", sort: (r) => r.volume_proof.units_per_day },
  { label: "Buyers (7d)", sort: (r) => r.volume_proof.distinct_buyers_7d },
  { label: "Last sale age", sort: (r) => r.volume_proof.last_sale_age_h, render: (r) => age(r.volume_proof.last_sale_age_h) },
  { label: "Data age", sort: (r) => r.data_age_h, render: (r) => <Badge tone={ageTone(r.data_age_h)}>{age(r.data_age_h)}</Badge> },
];

const marketCommand = (world: string) => `/li ${world.toLowerCase()} market`;

export default function Scan({ settings }: { settings: Settings }) {
  const [draft, setDraft] = useState<Partial<Params>>({});
  const [travelDraft, setTravelDraft] = useState<ReturnType<typeof travelEntries>>();
  const [result, setResult] = useState<Schema<"ScanResult">>();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(new Set<number>());
  const [sort, setSort] = useState({ column: 9, descending: true });
  const [copyState, setCopyState] = useState({ message: "", fallback: "" });

  const params: Params = {
    home_world: settings.home_world, buy_region: settings.buy_region,
    preset: "flip", min_ppu: 0, min_profit: 5000, buyer_tax: 0.05, seller_tax: 0.05,
    sellable_days: 3, ...draft,
  };
  const travel = travelDraft ?? travelEntries(settings.travel_cost);

  function update<K extends keyof Params>(key: K, value: Params[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function run(event: FormEvent) {
    event.preventDefault();
    setError("");
    setCopyState({ message: "", fallback: "" });
    setRunning(true);
    try {
      const home_world = params.home_world.trim();
      const buy_region = params.buy_region.trim();
      if (!home_world || !buy_region) throw new Error("Enter a home world and buy region.");
      const next = await api.scan({ ...params, home_world, buy_region, travel_cost: travelCosts(travel) });
      setResult(next);
      setSelected(new Set());
      setCopyState({ message: "", fallback: "" });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setRunning(false);
    }
  }

  function toggle(index: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopyState({ message: "Copied to clipboard.", fallback: "" });
    } catch {
      setCopyState({ message: "Clipboard unavailable. Select and copy the text below.", fallback: text });
    }
  }

  function copyRoute() {
    if (!result) return;
    const stops = new Map<string, string[]>();
    for (const [index, row] of result.rows.entries()) {
      if (!selected.has(index)) continue;
      const world = row.buy_world.toLowerCase();
      const items = stops.get(world) ?? [];
      items.push(`- ${row.item.name} (${row.item.hq ? "HQ" : "NQ"}) ×${row.sellable_qty} at ${gil(row.buy_ppu)} gil`);
      stops.set(world, items);
    }
    void copy([...Array.from(stops, ([world, items]) => [marketCommand(world), ...items].join("\n")), marketCommand(result.params.home_world)].join("\n\n"));
  }

  const rows = (result?.rows ?? []).map((row, index) => ({ row, index })).sort((a, b) => {
    const read = columns[sort.column].sort;
    const left = read(a.row);
    const right = read(b.row);
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    const order = typeof left === "number" && typeof right === "number"
      ? left - right : String(left).localeCompare(String(right));
    return sort.descending ? -order : order;
  });

  return <>
    <PageTitle title="Market scan" description="Find regional listings you can resell on your home world." />
    <Section title="Scan parameters">
      <form className="panel-body" onSubmit={run}>
        <fieldset disabled={running}>
          <legend className="sr-only">Scan parameters</legend>
          <div className="preset-grid" role="group" aria-label="Scan preset">
            {presets.map((preset) => <button key={preset.id} className="preset" type="button"
              aria-pressed={params.preset === preset.id} onClick={() => update("preset", preset.id)}>
              <span>{preset.name}</span><small>{preset.detail}</small>
            </button>)}
          </div>
          <div className="fields scan-fields">
            <Field label="Home world"><input required value={params.home_world} onChange={(e) => update("home_world", e.target.value)} /></Field>
            <Field label="Buy region"><input required value={params.buy_region} onChange={(e) => update("buy_region", e.target.value)} /></Field>
            <Field label="Minimum buy price (gil)"><Numeric value={params.min_ppu ?? 0} onValue={(value) => update("min_ppu", value)} /></Field>
            <Field label="Minimum profit / unit (gil)"><Numeric value={params.min_profit ?? 0} onValue={(value) => update("min_profit", value)} /></Field>
            <Field label="Buyer tax (%)"><Numeric max={100} step="any" value={Number(((params.buyer_tax ?? 0.05) * 100).toFixed(10))} onValue={(value) => update("buyer_tax", value / 100)} /></Field>
            <Field label="Seller tax (%)"><Numeric max={100} step="any" value={Number((params.seller_tax * 100).toFixed(10))} onValue={(value) => update("seller_tax", value / 100)} /></Field>
            <Field label="Sellable days"><Numeric min={0.01} step="any" value={params.sellable_days ?? 3} onValue={(value) => update("sellable_days", value)} /></Field>
          </div>
          <details className="travel-details" onInvalidCapture={(event) => { event.currentTarget.open = true; }}><summary>Travel costs <small>{travel.length} data centres</small></summary>
            <TravelEditor entries={travel} onChange={setTravelDraft} />
          </details>
          <div className="form-actions">
            <button className="primary" type="submit">{running ? "Scanning…" : "Run scan"}</button>
            {running && <Loading>Checking regional listings…</Loading>}
          </div>
        </fieldset>
      </form>
    </Section>
    {error && <Notice tone="error">{error}</Notice>}
    <Section title="Scan results" detail={result ? `${result.rows.length} matches · ${time(result.finished_ts)}` : undefined}
      action={result && result.rows.length > 0 && <button type="button" disabled={selected.size === 0 || running} onClick={copyRoute}>Copy route ({selected.size})</button>}>
      {result?.warnings?.map((warning, index) => <Notice key={index} tone="warn">{warning}</Notice>)}
      {copyState.message && <Notice>{copyState.message}{copyState.fallback && <textarea aria-label="Copy text" rows={5} readOnly value={copyState.fallback} onFocus={(e) => e.currentTarget.select()} />}</Notice>}
      {rows.length ? <Table label="Scan results" className="scan-table">
        <thead><tr>
          <th scope="col"><input type="checkbox" aria-label="Select all results" checked={selected.size === rows.length}
            ref={(el) => { if (el) el.indeterminate = selected.size > 0 && selected.size < rows.length; }}
            onChange={(e) => setSelected(e.target.checked ? new Set(rows.map(({ index }) => index)) : new Set())} /></th>
          {columns.map((col, index) => <th key={col.label} scope="col"
            aria-sort={sort.column === index ? sort.descending ? "descending" : "ascending" : undefined}>
            <button type="button" className="sort-button" onClick={() => setSort({
              column: index, descending: sort.column === index ? !sort.descending : typeof col.sort(rows[0].row) === "number",
            })}>{col.label}{sort.column === index && <span aria-hidden="true">{sort.descending ? " ↓" : " ↑"}</span>}</button>
          </th>)}
          <th scope="col">Lifestream</th>
        </tr></thead>
        <tbody>{rows.map(({ row, index }) => <tr key={index}>
          <td><input type="checkbox" aria-label={`Select ${row.item.name} ${row.item.hq ? "HQ" : "NQ"} on ${row.buy_world}`}
            checked={selected.has(index)} onChange={() => toggle(index)} /></td>
          {columns.map((col) => <td key={col.label}>{col.render ? col.render(row) : number(col.sort(row) as number | null)}</td>)}
          <td><button type="button" aria-label={`Copy ${marketCommand(row.buy_world)} for ${row.item.name}`}
            onClick={() => void copy(marketCommand(row.buy_world))}>Copy command</button></td>
        </tr>)}</tbody>
      </Table> : <Notice>{result ? "No listings matched. Try adjusting the scan parameters." : running ? "Waiting for scan results…" : "Run a scan to see matching listings here."}</Notice>}
    </Section>
  </>;
}
