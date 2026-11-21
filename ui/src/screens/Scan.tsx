import { Fragment, useEffect, useMemo, useState } from "react";
import { PartnerBadge, usePartnerFilter, visiblePartnerRow } from "../Partner";
import { api } from "../api/client";
import { Item, Notice, PageTitle, Section, Table, age, errorMessage, gil, number, time } from "../common";
import type { Schema } from "../common";
import { checkQuotes, route } from "../Travel";
import { purchasePlan } from "../purchasePlan";
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
  { label: "Median net/unit", group: "Profit", value: r => r.median_profit_per_unit, format: gil },
  { label: "Median capped net", group: "Profit", value: r => r.median_profit_per_unit == null ? null : r.median_profit_per_unit * r.sellable_qty, format: gil },
  { label: "Observed exit", group: "Profit", value: r => r.ask_exit_ppu ?? (r.home_lowest == null ? "No observed ask" : null) },
  { label: "Ask depth", group: "Profit", value: r => r.home_lowest_units },
  { label: "Ask net/unit", group: "Profit", value: r => r.ask_profit_per_unit, format: gil },
  { label: "Ask capped net", group: "Profit", value: r => r.ask_profit_capped, format: gil },
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
  "Median net/unit", "Median capped net", "Observed exit", "Ask depth", "Ask net/unit", "Ask capped net", "Qty at price", "Top buyer",
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
function heading(label: string, home: string, archived = false) {
  const ageLabel = label === "Data age" ? "Buy listing last seen" : label === "Last sale" ? "Last " + home + " sale" : "";
  return ageLabel ? ageLabel + (archived ? " at scan time" : "") : label;
}
function Detail({ row, home, archived }: { row: Row; home: string; archived: boolean }) {
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
                    <dt>{heading(c.label, home, archived)}</dt>
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
export function ScanResults({ result, archived = false, retry, edit }: { result: Result; archived?: boolean; retry?: () => void; edit?: () => void }) {
  const {hide,control} = usePartnerFilter();
  const [visible, setVisible] = useState(readColumns);
  const [selected, setSelected] = useState<number[]>([]);
  const [expanded, setExpanded] = useState<number>();
  const [sort, setSort] = useState({ label: "", desc: true });
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
  const home = result.params.home_world;
  const sorted = useMemo(() => {
    const rows = result.rows.map((row, i) => ({ row, i })).filter(({row}) => visiblePartnerRow(row, hide));
    const col = definitions.find(c => c.label === sort.label);
    if (!col) return rows;
    return rows.sort((a, b) => {
      const x = col.value(a.row),
        y = col.value(b.row);
      if (x == null || y == null) return Number(x == null) - Number(y == null);
      return (
        (sort.desc ? -1 : 1) *
        (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y)))
      );
    });
  }, [result.rows, sort, hide]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(undefined); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  const [skippedStacks, setSkippedStacks] = useState<Set<string>>(new Set());
  const selectedIds = new Set(selected);
  const chosen = sorted.filter(row => selectedIds.has(row.i));
  const all = chosen.length > 0 && chosen.length === sorted.length;
  const mixed = chosen.length > 0 && !all;
  const {preview, purchases, travel, outlay, net, bandFailed} = purchasePlan(chosen, result.params, skippedStacks);
  const quote = (r: Row) => ({...r, home, maxAge: result.params.max_data_age_h ?? undefined, historyEntries: result.params.history_entries});
  const [checking, setChecking] = useState(false);
  async function copy(onlyRoute: boolean) {
    if (onlyRoute) {
      setChecking(true);
      try { if (!await checkQuotes(purchases.map(({row, stacks}) => ({...quote(row), plannedStacks: stacks})), home)) return; }
      catch (e) { setNotice(errorMessage(e)); return; }
      finally { setChecking(false); }
    }
    const stops = new Map<string, string[]>();
    for (const { row, qty } of purchases) {
      const items = stops.get(row.buy_world) ?? [];
      items.push(
        `• ${row.item.name} (${row.item.hq ? "HQ" : "NQ"}) ×${qty} @ ${gil(row.buy_ppu)} gil`,
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
      {control}
      {result.warnings?.map((w, i) => (
        <Notice key={i} tone="warn">
          {w}
        </Notice>
      ))}
      <div className="panel-body">
        <details>
          <summary>Columns</summary><button onClick={() => setVisible(initial)}>Restore default columns</button>
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
                      {heading(c.label, home, archived)}
                    </label>
                  ))}
              </fieldset>
            ))}
          </div>
        </details>
        <p>Ordered by {sort.label ? heading(sort.label, home, archived) + (sort.desc ? " ↓" : " ↑") :
          "profile · " + (["volume","safe_flip"].includes(result.params.preset ?? "") ? "profit/day" : result.params.preset === "snipe" ? "profit/unit" : "capped profit")}.
          {" "}<button onClick={() => setSort({label:"", desc:true})}>Profile order</button></p>
        {!!chosen.length && <details open><summary>Whole-stack purchase preview</summary>
          {preview.map(({row, i, qty, units}) => <div key={i}><strong>{row.item.name} · {row.buy_world}</strong>
            {row.buy_stacks?.map((n,j) => <label className="check" key={j}><input type="checkbox"
              checked={!skippedStacks.has(i+"/"+j)} onChange={() => setSkippedStacks(previous => {
                const next = new Set(previous), key = i+"/"+j;
                if (next.has(key)) next.delete(key); else next.add(key);
                return next;
              })}/>Stack of {n}</label>) ?? <span>Stack sizes unavailable at scan time · estimated outlay</span>}
            <small>{units} sellable · {qty-units} leftover</small></div>)}
          <p>{gil(outlay)} gil taxed outlay + {gil(travel)} travel = <strong>{gil(outlay+travel)} gil to bring</strong></p>
          <p>Window net after funding all stacks · {gil(net)} gil ({number(outlay ? net/outlay : null)}×).
            Uses the lower exit when observed, otherwise the median.</p>
          {bandFailed && <Notice tone="warn">Route below your large-purchase return target.</Notice>}
        </details>}
        {!!result.rejected?.length && <details><summary>Rejected candidates ({result.rejected.length})</summary>
          {result.rejected.map((r,i) => <p key={i}>{r.item.name} {r.item.hq ? "HQ" : "NQ"} {r.buy_world} · {r.reasons.join(", ")}</p>)}</details>}
        <div className="form-actions">
          <button disabled={!purchases.length} onClick={() => void copy(false)}>
            Copy results ({chosen.length})
          </button>
          <button disabled={checking || !purchases.length} onClick={() => void copy(true)}>
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
        <Table label="Scan results" className="scan-table">
          <thead>
            <tr>
              <th className="select-column">
                <input
                  type="checkbox"
                  aria-label={`Select all visible results (${chosen.length} of ${sorted.length} selected)`}
                  checked={all}
                  ref={input => { if (input) input.indeterminate = mixed; }}
                  onChange={e => {
                    const checked = e.target.checked;
                    const visibleIds = new Set(sorted.map(row => row.i));
                    setSelected(previous => checked ? [...new Set([...previous, ...visibleIds])]
                      : previous.filter(id => !visibleIds.has(id)));
                  }}
                />
              </th>
              {columns.map((c) => (
                <th key={c.label} className={c.label === "Item" ? "item-column" : undefined}>
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
                    {heading(c.label, home, archived)}
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
                  <td className="select-column" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      aria-label={`Select ${row.item.name}`}
                      checked={selectedIds.has(i)}
                      onChange={(e) =>
                        setSelected(e.target.checked ? [...selected, i] : selected.filter((v) => v !== i))
                      }
                    />
                  </td>
                  {columns.map((c) => (
                    <td key={c.label} className={c.label === "Item" ? "item-column" : undefined}>
                      {c.label === "Item" ? (
                        <>
                          <button
                            title="Toggle details (Esc to close)" aria-expanded={expanded === i}
                            aria-label={`Details for ${row.item.name}`}
                            className="sort-button"
                          >
                            {expanded === i ? "▾" : "▸"}
                          </button>
                          <PartnerBadge row={row}/><Item item={row.item} buyWorld={row.buy_world} quote={quote(row)} />
                          {row.ask_below_floor && <small className="warning-text">Below floor at observed ask</small>}
                          {row.large_buy_below_multiple && <small className="warning-text">Large buy below return target</small>}
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
                      <Detail row={row} home={home} archived={archived} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      ) : (
        <Notice>{result.warnings?.length ? <>No confirmed matches in the data received · {result.warnings.length} warnings. {retry && <button onClick={retry}>Retry</button>}</> :
          <>No matches for {result.params.quality} quality in {result.params.buy_region} with a {gil(result.params.min_profit)} gil/unit floor. {edit && <button onClick={edit}>Edit profile</button>}</>}</Notice>
      )}
    </Section>
  );
}

export default function Scan({ settings }: { settings: Schema<"UserSettings-Output"> }) {
  const [result, setResult] = useState<Result>();
  const [revision, setRevision] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [profile, setProfile] = useState("");
  async function run(params: Schema<"ScanParams">, name: string) {
    setProfile(name);
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
      {result && <ScanResults key={revision} result={result} retry={running ? undefined : () => void run(result.params,profile)} edit={() => document.querySelector<HTMLButtonElement>(".profile-editor .edit-profile")?.click()} />}
    </>
  );
}
