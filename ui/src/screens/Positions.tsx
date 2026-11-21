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
  const totals = pnl.data && [
    { label: "Realised profit", value: pnl.data.realised_profit, tone: pnl.data.realised_profit >= 0 ? "profit" : "loss" },
    { label: "Net sales proceeds", value: pnl.data.sales_proceeds },
    { label: "Known cost of goods sold", value: pnl.data.known_cost_of_goods_sold },
    { label: "Units sold with unknown cost", value: pnl.data.unknown_cost_qty_sold, quantity: true },
  ];

  return <>
    <PageTitle title="Positions" description="Monitor inventory, realised profit and the plugin data behind your ledger." />
    <Section title="Profit & loss">
      {totals ? <dl className="metric-grid panel-body">
        {totals.map((metric) => <div key={metric.label}>
          <dt>{metric.label}</dt><dd className={metric.tone}>{gil(metric.value)}{!metric.quantity && <small> gil</small>}</dd>
        </div>)}
      </dl> : <ResourceState error={pnl.error} retry={pnl.retry} loading="Loading profit and loss…" />}
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
    <Section title="Open positions" detail={open ? `${open.length} item variants held` : undefined}>
      {open ? open.length ? <Table label="Open positions">
        <thead><tr>{["Item", "Open qty", "Listed qty", "Average cost", "Cost basis", "Realised profit"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{open.map((row) => <tr key={`${row.item.id}-${row.item.hq}`}>
          <td><Item item={row.item} /></td><td>{gil(row.qty_open)}</td><td>{gil(row.listed_qty)}</td><td>{gil(row.avg_cost)}</td>
          <td><Badge tone={row.cost_known ? "good" : "warn"}>{row.cost_known ? "Known" : "Unknown"}</Badge></td>
          <td><strong className={row.realised_profit >= 0 ? "profit" : "loss"}>{gil(row.realised_profit)}</strong></td>
        </tr>)}</tbody>
      </Table> : <Notice>No open positions found.</Notice> : <ResourceState error={positions.error} retry={positions.retry} loading="Loading positions…" />}
    </Section>
  </>;
}
