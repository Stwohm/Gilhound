import { Fragment, useEffect, useState } from "react";
import { api } from "../api/client";
import { Item, Notice, PageTitle, Section, Table, age, errorMessage, gil, number, time } from "../common";
import type { Schema } from "../common";
import { route } from "../Travel";
import ProfileEditor from "./ProfileEditor";

type Row = Schema<"ScanRow">;
type Result = Schema<"ScanResult">;
type Value = number | string | null | undefined;
type Column = { label: string; group: string; value: (r: Row) => Value; format?: (v: number) => string };
const pct = (v: number) => number(v) + "%";
const definitions: Column[] = [
  { label: "Item", group: "Purchase", value: (r) => r.item.name },
  { label: "Buy world", group: "Purchase", value: (r) => r.buy_world },
  { label: "DC", group: "Purchase", value: (r) => r.buy_dc },
  { label: "Buy price", group: "Purchase", value: (r) => r.buy_ppu, format: gil },
  { label: "Qty at price", group: "Purchase", value: (r) => r.buy_qty_at_price },
  { label: "Listings within 10%", group: "Purchase", value: (r) => r.nearby_listings },
  { label: "Units within 10%", group: "Purchase", value: (r) => r.nearby_units },
  { label: "Second price", group: "Purchase", value: (r) => r.second_buy_price },
  { label: "Next listing gap", group: "Purchase", value: (r) => r.next_listing_gap },
  { label: "Data age", group: "Purchase", value: (r) => r.data_age_h, format: age },
  { label: "Home median", group: "Sales · 7 days", value: (r) => r.home_median_7d },
  {
    label: "Weekly trend",
    group: "Sales · 7 days",
    value: (r) => r.trend_pct,
    format: (v) => (v > 0 ? "↑ " : v < 0 ? "↓ " : "") + pct(Math.abs(v)),
  },
  { label: "Units sold", group: "Sales · 7 days", value: (r) => r.units_sold_7d },
  { label: "Sales", group: "Sales · 7 days", value: (r) => r.sales_7d },
  { label: "Units/day", group: "Sales · 7 days", value: (r) => r.volume_proof.units_per_day },
  { label: "Buyers (7d)", group: "Sales · 7 days", value: (r) => r.volume_proof.distinct_buyers_7d },
  {
    label: "Top buyer",
    group: "Sales · 7 days",
    value: (r) => r.volume_proof.top_buyer_share,
    format: (v) => pct(100 * v),
  },
  { label: "Days with sales", group: "Sales · 7 days", value: (r) => r.volume_proof.days_with_sales },
  { label: "Average sale size", group: "Sales · 7 days", value: (r) => r.average_sale_size },
  { label: "Min sale price", group: "Sales · 7 days", value: (r) => r.min_sale_price },
  { label: "Max sale price", group: "Sales · 7 days", value: (r) => r.max_sale_price },
  { label: "HQ share", group: "Sales · 7 days", value: (r) => r.hq_sale_share, format: (v) => pct(100 * v) },
  { label: "Last sale", group: "Sales · 7 days", value: (r) => r.volume_proof.last_sale_age_h, format: age },
  { label: "Home listings", group: "Competition", value: (r) => r.home_listing_count },
  { label: "Sellers", group: "Competition", value: (r) => r.distinct_sellers },
  { label: "Rival stock", group: "Competition", value: (r) => r.rival_stock },
  { label: "Home lowest", group: "Competition", value: (r) => r.home_lowest },
  { label: "Units below target", group: "Competition", value: (r) => r.units_below_sell },
  { label: "Profit/unit", group: "Profit", value: (r) => r.profit_per_unit, format: gil },
  { label: "ROI", group: "Profit", value: (r) => r.roi_pct, format: pct },
  { label: "Sellable qty", group: "Profit", value: (r) => r.sellable_qty },
  { label: "Capital needed", group: "Profit", value: (r) => r.capital_needed, format: gil },
  { label: "Capped profit", group: "Profit", value: (r) => r.total_profit_capped, format: gil },
  { label: "Profit/day", group: "Profit", value: (r) => r.profit_per_day, format: gil },
  { label: "Days to sell", group: "Profit", value: (r) => r.days_to_sell },
  { label: "Wait incl. rivals (days)", group: "Profit", value: (r) => r.est_wait_days },
];
const groups = [...new Set(definitions.map((c) => c.group))];
const initial = [
  "Item",
  "Buy world",
  "Buy price",
  "Home median",
  "Profit/unit",
  "ROI",
  "Units/day",
  "Buyers (7d)",
  "Sellers",
  "Rival stock",
  "Last sale",
  "Data age",
];
function readColumns(): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem("scanColumns") ?? "null");
    if (Array.isArray(saved)) return [...new Set(["Item", ...saved.filter((v) => typeof v === "string")])];
  } catch {
    /* Storage may be disabled. */
  }
  return initial;
}
function display(c: Column, row: Row) {
  const v = c.value(row);
  return typeof v === "number" ? (c.format ?? number)(v) : (v ?? "—");
}
function Detail({ row, home }: { row: Row; home: string }) {
  const units = row.daily_units_14d;
  return (
    <div className="scan-detail">
      <strong>
        {row.item.name} · {home}
      </strong>
      {units?.length === 14 && (
        <figure>
          <figcaption>Daily units sold · past 14 days</figcaption>
          <svg width="210" height="44" viewBox="0 0 210 44" role="img" aria-label={units.join(", ")}>
            <polyline
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              points={units
                .map((v, i) => `${2 + i * 15.8},${41 - (38 * v) / Math.max(1, ...units)}`)
                .join(" ")}
            />
          </svg>
        </figure>
      )}
      <div className="metric-groups">
        {groups.map((group) => (
          <div key={group}>
            <strong>{group}</strong>
            <dl>
              {definitions
                .filter((c) => c.group === group)
                .map((c) => (
                  <Fragment key={c.label}>
                    <dt>{c.label}</dt>
                    <dd>{display(c, row)}</dd>
                  </Fragment>
                ))}
            </dl>
          </div>
        ))}
      </div>
    </div>
  );
}
export function ScanResults({ result }: { result: Result }) {
  const [visible, setVisible] = useState(readColumns);
  const [selected, setSelected] = useState<number[]>([]);
  const [expanded, setExpanded] = useState<number>();
  const [sort, setSort] = useState({ label: "Capped profit", desc: true });
  const [notice, setNotice] = useState("");
  const [fallback, setFallback] = useState("");
  useEffect(() => {
    try {
      localStorage.setItem("scanColumns", JSON.stringify(visible));
    } catch {
      /* optional */
    }
  }, [visible]);
  const columns = visible.flatMap((label) => definitions.filter((c) => c.label === label));
  const sorted = result.rows
    .map((row, i) => ({ row, i }))
    .sort((a, b) => {
      const col = definitions.find((c) => c.label === sort.label)!;
      const x = col.value(a.row),
        y = col.value(b.row);
      if (x == null || y == null) return Number(x == null) - Number(y == null);
      return (
        (sort.desc ? -1 : 1) *
        (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y)))
      );
    });
  async function copy(onlyRoute: boolean) {
    const stops = new Map<string, string[]>();
    for (const { row, i } of sorted) {
      if (!selected.includes(i)) continue;
      const items = stops.get(row.buy_world) ?? [];
      items.push(
        `• ${row.item.name} (${row.item.hq ? "HQ" : "NQ"}) ×${row.sellable_qty} @ ${gil(row.buy_ppu)} gil · profit ${gil(row.total_profit_capped)} gil`,
      );
      stops.set(row.buy_world, items);
    }
    const text = onlyRoute
      ? [...Array.from(stops.keys(), route), route(result.params.home_world)].join("\n")
      : Array.from(stops, ([world, items]) => [world, ...items].join("\n")).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setNotice("Copied");
      setFallback("");
    } catch {
      setNotice("Select and copy the text below.");
      setFallback(text);
    }
  }
  return (
    <Section title="Scan results" detail={`${result.rows.length} matches · ${time(result.finished_ts)}`}>
      {result.warnings?.map((w, i) => (
        <Notice key={i} tone="warn">
          {w}
        </Notice>
      ))}
      <div className="panel-body">
        <details>
          <summary>Columns</summary>
          <div className="metric-groups">
            {groups.map((group) => (
              <fieldset key={group}>
                <legend>{group}</legend>
                {definitions
                  .filter((c) => c.group === group)
                  .map((c) => (
                    <label className="check" key={c.label}>
                      <input
                        type="checkbox"
                        disabled={c.label === "Item"}
                        checked={visible.includes(c.label)}
                        onChange={(e) =>
                          setVisible(
                            e.target.checked ? [...visible, c.label] : visible.filter((v) => v !== c.label),
                          )
                        }
                      />
                      {c.label}
                    </label>
                  ))}
              </fieldset>
            ))}
          </div>
        </details>
        <div className="form-actions">
          <button disabled={!selected.length} onClick={() => void copy(false)}>
            Copy results ({selected.length})
          </button>
          <button disabled={!selected.length} onClick={() => void copy(true)}>
            Copy route
          </button>
        </div>
        {notice && (
          <Notice>
            {notice}
            {fallback && <textarea readOnly value={fallback} />}
          </Notice>
        )}
      </div>
      {sorted.length ? (
        <Table label="Scan results">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="Select all results"
                  checked={selected.length === sorted.length}
                  onChange={(e) => setSelected(e.target.checked ? sorted.map((x) => x.i) : [])}
                />
              </th>
              {columns.map((c) => (
                <th key={c.label}>
                  <button
                    className="sort-button"
                    onClick={() =>
                      setSort({
                        label: c.label,
                        desc:
                          c.label === sort.label ? !sort.desc : typeof c.value(sorted[0].row) === "number",
                      })
                    }
                  >
                    {c.label}
                    {sort.label === c.label ? (sort.desc ? " ↓" : " ↑") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ row, i }) => (
              <Fragment key={i}>
                <tr className="expandable" onClick={() => setExpanded(expanded === i ? undefined : i)}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      aria-label={`Select ${row.item.name}`}
                      checked={selected.includes(i)}
                      onChange={(e) =>
                        setSelected(e.target.checked ? [...selected, i] : selected.filter((v) => v !== i))
                      }
                    />
                  </td>
                  {columns.map((c) => (
                    <td key={c.label}>
                      {c.label === "Item" ? (
                        <>
                          <button
                            aria-expanded={expanded === i}
                            aria-label={`Details for ${row.item.name}`}
                            className="sort-button"
                          >
                            {expanded === i ? "▾" : "▸"}
                          </button>
                          <Item item={row.item} buyWorld={row.buy_world} />
                        </>
                      ) : (
                        display(c, row)
                      )}
                    </td>
                  ))}
                </tr>
                {expanded === i && (
                  <tr>
                    <td colSpan={columns.length + 1}>
                      <Detail row={row} home={result.params.home_world} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      ) : (
        <Notice>No listings matched these settings.</Notice>
      )}
    </Section>
  );
}

export default function Scan({ settings }: { settings: Schema<"UserSettings-Output"> }) {
  const [result, setResult] = useState<Result>();
  const [revision, setRevision] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  async function run(params: Schema<"ScanParams">, name: string) {
    setRunning(true);
    setError("");
    try {
      setResult(await api.scan(params, name));
      setRevision((r) => r + 1);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRunning(false);
    }
  }
  return (
    <>
      <PageTitle
        title="Market scan"
        description="Find regional listings you can resell on your home world."
      />
      <Section title="Scan parameters">
        <ProfileEditor
          key={JSON.stringify([settings.home_world, settings.buy_region, settings.travel_cost])}
          library="scan"
          running={running}
          onRun={run}
        />
      </Section>
      {error && <Notice tone="error">{error}</Notice>}
      {result && <ScanResults key={revision} result={result} />}
    </>
  );
}
