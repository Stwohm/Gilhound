import { useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import {
  Badge, Item, Notice, PageTitle, Section, Table,
  age, ageTone, errorMessage, gil, number, time,
} from "../common";
import type { Schema, Settings } from "../common";

type Params = Schema<"ScanParams">;
import ProfileEditor from "./ProfileEditor";
type Row = Schema<"ScanRow">;
type Column = { label: string; sort: (row: Row) => string | number | null; render?: (row: Row) => ReactNode };


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
  const [result, setResult] = useState<Schema<"ScanResult">>();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(new Set<number>());
  const [sort, setSort] = useState({ column: 9, descending: true });
  const [copyState, setCopyState] = useState({ message: "", fallback: "" });

  async function run(params: Params) {
    setError("");
    setCopyState({ message: "", fallback: "" });
    setRunning(true);
    try {
      const next = await api.scan(params);
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
      <ProfileEditor key={JSON.stringify([settings.home_world, settings.buy_region, settings.travel_cost])} library="scan" running={running} onRun={run} />
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
