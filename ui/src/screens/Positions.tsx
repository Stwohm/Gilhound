import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Badge, Item, Notice, PageTitle, ResourceState, Section, Table, age, epoch, gil, time, useResource,
} from "../common";
import type { Schema } from "../common";

const sourceNames: Record<Schema<"SourceName">, string> = {
  cashflow: "CashFlow",
  allagan_market: "AllaganMarket",
  allagan_tools: "Allagan Tools",
  restocky: "Restocky",
};

export default function Positions() {
  const sources = useResource(api.getSources);
  const positions = useResource(api.getPositions);
  const open = positions.data?.filter((row) => row.qty_open > 0);
  const hidden = useResource(api.getHidden);
  const [view, setView] = useState("Open");
  const [unlisted, setUnlisted] = useState(false);
  const [needsCost, setNeedsCost] = useState(false);
  const [undo, setUndo] = useState<Schema<"ItemRef">>();
  useEffect(() => {
    if (!undo) return;
    const timer = window.setTimeout(() => setUndo(undefined), 5000);
    return () => window.clearTimeout(timer);
  }, [undo]);
  async function personal(item: Schema<"ItemRef">, value: boolean) {
    setSaving(true);
    setError("");
    setUndo(undefined);
    const before = positions.data;
    const previousHidden = hidden.data;
    const different = (other: Schema<"ItemRef">) => other.id !== item.id || other.hq !== item.hq;
    if (value) positions.setData(before?.filter(row => different(row.item)));
    if (previousHidden) hidden.setData(value ? [...previousHidden, item] : previousHidden.filter(different));
    try {
      await api.setPersonal({ item_id: item.id, hq: item.hq, personal: value });
    } catch (e) {
      positions.setData(before); hidden.setData(previousHidden); setSaving(false);
      setError(e instanceof Error ? e.message : "Could not update position.");
      return;
    }
    try {
      await Promise.all([
        ...(!value ? [api.getPositions().then(positions.setData)] : []),
        ...(!previousHidden ? [api.getHidden().then(hidden.setData)] : [])]);
    } catch { setError("Change saved. Refresh to reload the latest totals and positions."); }
    finally { setSaving(false); }
    if (value) setUndo(item);
  }
  const [editing, setEditing] = useState("");
  const [cost, setCost] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function setGathered(row: Schema<"Position">) {
    setSaving(true);
    setError("");
    try {
      await api.setGathered({ item_id: row.item.id, hq: row.item.hq, gathered: !row.gathered });
      positions.retry();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update Gathered status.");
    } finally {
      setSaving(false);
    }
  }
  const holdings = open?.filter(r => unlisted || r.listed_qty > 0);
  const known = holdings?.filter(r => r.cost_known).length ?? 0;
  const rows = holdings?.filter(r => !needsCost || !r.cost_known);
  async function save(item: Schema<"ItemRef">) {
    setSaving(true);
    try {
      await api.setCost({ item_id: item.id, hq: item.hq, unit_cost: Number(cost) });
      positions.retry();
      setEditing("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the cost.");
    } finally {
      setSaving(false);
    }
  }
  const totals = open && [
    { label: "Active listed item qualities", value: open.filter(p=>p.listed_qty>0).length, quantity: true },
    { label: "Listed", value: open.reduce((sum, row) => sum + row.listed_qty, 0), quantity: true,
      listings: open.reduce((sum, row) => sum + (row.listing_count ?? 0), 0) },
    { label: "Known flip capital", value: open.reduce((sum, row) => sum + row.known_capital, 0) },
  ];

  return <>
    <PageTitle title="Positions" description="Active retainer listings and their acquisition costs." />
    <Section title="Holdings">
      {totals ? <dl className="metric-grid panel-body">
        {totals.map((metric) => <div key={metric.label} title={metric.listings != null ? "Units are the sum of the listed stack sizes." : undefined}>
          <dt>{metric.label}</dt><dd>{metric.listings != null
            ? gil(metric.listings) + " listings · " + gil(metric.value) + " units"
            : <>{gil(metric.value)}{!metric.quantity && <small> gil</small>}</>}</dd>
        </div>)}
      </dl> : <ResourceState error={positions.error} retry={positions.retry} loading="Loading holdings…" />}
    </Section>
    <Section title={view === "Open" ? "Active listings" : "Hidden positions"} action={<div>
      <button disabled={saving} onClick={() => { positions.retry(); hidden.retry(); sources.retry(); }}>Refresh</button>
      {["Open", "Hidden"].map(tab => <button key={tab} aria-pressed={view === tab}
        onClick={() => setView(tab)}>{tab}</button>)}
    </div>}>
      {error && <Notice tone="error">{error}</Notice>}
      {sources.data?.some(s => s.name === "allagan_tools" && !s.recognised) &&
        <Notice tone="warn">Allagan Tools inventory unavailable. Positions use purchase history and listings.</Notice>}
      {view !== "Hidden" && <div className="form-actions">
        <label><input type="checkbox" checked={unlisted} onChange={e=>setUnlisted(e.target.checked)} /> Show unlisted stock</label>
        <button aria-pressed={needsCost} onClick={()=>setNeedsCost(!needsCost)}>Needs cost</button>
        <span>Known cost · {known} / {holdings?.length ?? 0} item qualities</span></div>}
      {view === "Hidden" ? hidden.data ? hidden.data.length ? <Table label="Hidden personal items">
        <thead><tr><th>Item</th><th>Action</th></tr></thead><tbody>{hidden.data.map(item =>
          <tr key={item.id + "-" + item.hq}><td><Item item={item} /></td><td><button disabled={saving}
          onClick={() => void personal(item, false)}>Unhide</button></td></tr>)}</tbody></Table>
        : <Notice>No hidden personal items.</Notice> : <ResourceState error={hidden.error} retry={hidden.retry} loading="Loading hidden items…" />
      : rows ? rows.length ? <Table className="positions-table" label="Open positions">
        <thead><tr>{["Item", "Quality", "Open qty", "Listed qty", "Lowest list price", "Oldest listing", "Home lowest / rank", "Undercut observations", "Average cost", "Action"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{rows.map((row) => <tr key={`${row.item.id}-${row.item.hq}`}>
          <td><Item item={row.item} showQuality={false} /></td><td>{row.item.hq ? "HQ" : "NQ"}</td><td>{gil(row.qty_open)}</td><td>{gil(row.listed_qty)}</td><td>{gil(row.lowest_list_price)}</td>
          <td>{row.first_listed ? age(Math.max(0,epoch()-row.first_listed)/3600) : "—"}</td><td title={row.home_observed_ts ? "Observed " + time(row.home_observed_ts) : undefined}>{gil(row.home_lowest)} / {row.home_rank ?? "—"}</td><td>{gil(row.undercut_count)}</td><td>{row.cost_known ? gil(row.avg_cost) : editing === `${row.item.id}-${row.item.hq}` ?
            <form onSubmit={e => { e.preventDefault(); void save(row.item); }}>
              <input aria-label="Unit cost in gil" required type="number" min="0" step="1" value={cost} onChange={e => setCost(e.target.value)} />
              <button disabled={saving}>Save</button><button type="button" onClick={() => setEditing("")}>Cancel</button>
            </form> : <>Unknown <button onClick={() => { setCost(""); setError(""); setEditing(`${row.item.id}-${row.item.hq}`); }}>Set cost</button></>}</td>
          <td><label><input type="checkbox" disabled={saving} checked={row.gathered}
            aria-label={`Gathered: ${row.item.name}`} onChange={() => void setGathered(row)} /> Gathered</label>{" "}
            <button disabled={saving} title="Hide (personal)" aria-label={`Hide (personal): ${row.item.name}`}
            onClick={() => void personal(row.item, true)}><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3l18 18M10 5c5-1 9 3 12 7l-3 4M6 6l-4 6c4 6 10 9 16 5" /></svg></button></td>
        </tr>)}</tbody>
      </Table> : <Notice>No positions match these listing and cost filters.</Notice> : <ResourceState error={positions.error} retry={positions.retry} loading="Loading positions…" />}
    </Section>
    {undo && <div className="position-toast" role="status">Hidden · <button disabled={saving}
      onClick={() => void personal(undo, false)}>Undo</button></div>}
    <Section title="Plugin sources">
      {sources.data ? sources.data.length ? <div className="source-grid panel-body">
        {sources.data.map((source) => {
          const hours = source.last_update_ts == null ? null : Math.max(0, epoch() - source.last_update_ts) / 3600;
          const days = hours == null ? 0 : Math.floor(hours / 24);
          return <article className="source-card" key={source.name}>
            <h3>{sourceNames[source.name]}</h3>
            <div className="source-tags">
              <Badge tone={source.found ? "good" : "warn"}>{source.found ? "Found" : "Missing"}</Badge>
              <Badge tone={source.recognised ? "good" : "muted"}>{source.recognised ? "Recognised" : "Unrecognised"}</Badge>
            </div>
            <p title={time(source.last_update_ts)}>Last update · {hours == null ? "Unknown" : days > 0 ? `${days} ${days === 1 ? "day" : "days"} ago` : `${age(hours)} ago`}</p>
            {source.warning && <p className="warning-text">{source.warning}</p>}
          </article>;
        })}
      </div> : <Notice>No plugin sources reported.</Notice> : <ResourceState error={sources.error} retry={sources.retry} loading="Checking plugin sources…" />}
    </Section>
  </>;
}
