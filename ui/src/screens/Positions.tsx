import { useState } from "react";
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
  const pnl = useResource(api.getPnl);
  const sources = useResource(api.getSources);
  const positions = useResource(api.getPositions);
  const open = positions.data?.filter((row) => row.qty_open > 0);
  const [closed, setClosed] = useState(false);
  const [editing, setEditing] = useState("");
  const [cost, setCost] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const rows = closed ? positions.data?.filter(row => row.qty_open === 0) : open;
  async function save(item: Schema<"ItemRef">) {
    setSaving(true);
    try {
      await api.setCost({ item_id: item.id, hq: item.hq, unit_cost: Number(cost) });
      positions.retry();
      pnl.retry();
      setEditing("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the cost.");
    } finally {
      setSaving(false);
    }
  }
  const totals = open && pnl.data && [
    { label: "Open positions", value: open.length, quantity: true },
    { label: "Listed units", value: open.reduce((sum, row) => sum + row.listed_qty, 0), quantity: true },
    { label: "Capital tied (known cost)", value: open.reduce((sum, row) => sum + row.known_capital, 0) },
    { label: "Realised profit", value: pnl.data.realised_profit, tone: pnl.data.realised_profit >= 0 ? "profit" : "loss" },
    { label: "Net sales proceeds", value: pnl.data.sales_proceeds },
    { label: "Known cost of goods sold", value: pnl.data.known_cost_of_goods_sold },
    { label: "Units sold with unknown cost", value: pnl.data.unknown_cost_qty_sold, quantity: true },
  ];

  return <>
    <PageTitle title="Positions" description="Track open flips and review realised profit by item and quality." />
    <Section title="Profit & loss">
      {totals ? <dl className="metric-grid panel-body">
        {totals.map((metric) => <div key={metric.label}>
          <dt>{metric.label}</dt><dd className={metric.tone}>{gil(metric.value)}{!metric.quantity && <small> gil</small>}</dd>
        </div>)}
      </dl> : <ResourceState error={pnl.error || positions.error} retry={() => { positions.retry(); pnl.retry(); }} loading="Loading profit and loss…" />}
    </Section>
    <Section title={closed ? "Closed positions" : "Open positions"} action={<div>
      <button onClick={() => { positions.retry(); pnl.retry(); }}>Refresh</button>
      <button aria-pressed={!closed} onClick={() => setClosed(false)}>Open</button>
      <button aria-pressed={closed} onClick={() => setClosed(true)}>Closed</button>
    </div>}>
      {rows ? rows.length ? <Table label={closed ? "Closed positions" : "Open positions"}>
        <thead><tr>{["Item", "Quality", "Open qty", "Listed qty", "Average cost", "Realised profit"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{rows.map((row) => <tr key={`${row.item.id}-${row.item.hq}`}>
          <td><Item item={row.item} showQuality={false} /></td><td>{row.item.hq ? "HQ" : "NQ"}</td><td>{gil(row.qty_open)}</td><td>{gil(row.listed_qty)}</td><td>{row.cost_known ? gil(row.avg_cost) : editing === `${row.item.id}-${row.item.hq}` ?
            <form onSubmit={e => { e.preventDefault(); void save(row.item); }}>
              <input aria-label="Unit cost in gil" required type="number" min="0" step="1" value={cost} onChange={e => setCost(e.target.value)} />
              <button disabled={saving}>Save</button><button type="button" onClick={() => setEditing("")}>Cancel</button>
              {error && <span role="alert">{error}</span>}
            </form> : <>Unknown <button onClick={() => { setCost(""); setError(""); setEditing(`${row.item.id}-${row.item.hq}`); }}>Set cost</button></>}</td>
          <td><strong className={row.realised_profit >= 0 ? "profit" : "loss"}>{gil(row.realised_profit)}</strong></td>
        </tr>)}</tbody>
      </Table> : <Notice>No {closed ? "closed" : "open"} positions found.</Notice> : <ResourceState error={positions.error} retry={positions.retry} loading="Loading positions…" />}
    </Section>
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
