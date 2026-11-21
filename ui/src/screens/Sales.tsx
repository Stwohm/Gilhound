import { useMemo, useState } from "react";
import { api } from "../api/client";
import { Field, Notice, PageTitle, ResourceState, Section, Table, gil, number, time, useResource } from "../common";
import type { Schema } from "../common";

type Sale = Schema<"RealisedSale">;
const quality = (s: Sale) => s.item.hq ? "HQ" : "NQ";
const rivals = (s: Sale) => Object.entries(s.rivals ?? {}).sort((a,b) => b[1]-a[1])
  .map(([name,count]) => name + " (" + count + ")").join(", ");
function exportSales(rows: Sale[]) {
  const header = ["UTC","Item ID","Item","Quality","Qty","Gross stack","Net stack","FIFO stack cost",
    "Unknown units","Realised profit (known units)","ROI %","Purchase days","Listing days","Reprices",
    "Own undercuts","Rival observations","Retainer","Sources"];
  const cells = rows.map(s => [new Date(s.ts*1000).toISOString(),s.item.id,s.item.name,quality(s),s.qty,
    s.gross,s.net,s.fifo_cost,s.unknown_qty,s.known_profit,s.roi_pct,s.purchase_days,s.listing_days,
    s.reprices,s.own_undercuts,rivals(s),s.retainer,s.sources.join(", ")]);
  const csv = [header,...cells].map(line => line.map(value => {
    let text = String(value ?? "");
    if (typeof value === "string" && /^[=+@\-\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"','""') + '"';
  }).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
  const a = document.createElement("a"); a.href=url; a.download="gilhound-sales.csv"; a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function Bars({title, buckets}: {title:string; buckets:[string,number][]}) {
  const max = Math.max(1,...buckets.map(([,v])=>Math.abs(v)));
  const signed = buckets.some(([,v])=>v<0);
  const base = signed ? 40 : 69;
  const width = 320 / Math.max(1,buckets.length);
  return <figure><figcaption>{title}</figcaption><svg role="img" aria-label={title} width="320" height="76" viewBox="0 0 320 76">
    <line x1="0" x2="320" y1={base} y2={base} stroke="currentColor" />
    {buckets.map(([label,v],i)=>{
      const height = Math.abs(v)/max*(base-4);
      return <rect key={label} x={i*width} y={v>=0 ? base-height : base} width={width*.84}
        height={height} fill={v>=0 ? "#54a98e":"#c57474"}><title>{label} · {gil(v)}</title></rect>;
    })}</svg><small>{!buckets.length ? "No known-cost observations" : buckets.length <= 4 ?
      buckets.map(([label,v])=>label+" · "+gil(v)).join(" | ") : buckets[0][0]+" — "+buckets.at(-1)?.[0]}</small></figure>;
}

export default function Sales() {
  const data = useResource(api.getSales);
  const sources = useResource(api.getSources);
  const [from,setFrom] = useState(""), [until,setUntil] = useState("");
  const [search,setSearch] = useState(""), [retainer,setRetainer] = useState("");
  const [rollup,setRollup] = useState(false), [weekly,setWeekly] = useState(false);
  const rows = useMemo(() => data.data?.filter(s =>
    (!from || s.ts >= Date.parse(from)/1000) && (!until || s.ts < Date.parse(until)/1000+86400) &&
    (!retainer || s.retainer === retainer) && (s.item.name.toLowerCase().includes(search.toLowerCase()) ||
    String(s.item.id) === search)) ?? [], [data.data,from,until,search,retainer]);
  const groups = new Map<string,{name:string; qty:number; count:number; profit:number; net:number;
    slotDays:number; timedProfit:number; known:number}>();
  const periods = new Map<number,number>(), distribution = [0,0,0,0];
  let totalNet=0, profit=0, duration=0, timed=0, unknown=0;
  for (const sale of rows) {
    const key=sale.item.id+"-"+sale.item.hq;
    const g=groups.get(key) ?? {name:sale.item.name+" "+quality(sale),qty:0,count:0,profit:0,
      net:0,slotDays:0,timedProfit:0,known:0};
    g.qty+=sale.qty; g.count++; g.profit+=sale.known_profit; g.net+=sale.net;
    g.known+=sale.qty-sale.unknown_qty;
    if (sale.listing_days != null) {
      duration+=sale.listing_days; timed++;
      distribution[sale.listing_days<1 ? 0 : sale.listing_days<3 ? 1 : sale.listing_days<7 ? 2 : 3]++;
      if (sale.fifo_cost != null && sale.listing_days>0) {g.slotDays+=sale.listing_days; g.timedProfit+=sale.known_profit;}
    }
    groups.set(key,g); totalNet+=sale.net; profit+=sale.known_profit; unknown+=sale.unknown_qty;
    const day=Math.floor(sale.ts/86400);
    const period=weekly ? day-(day+3)%7 : day;
    if (sale.unknown_qty<sale.qty) periods.set(period,(periods.get(period) ?? 0)+sale.known_profit);
  }
  const items=[...groups.values()].sort((a,b)=>b.profit-a.profit);
  const ranked=items.filter(g=>g.known>0);
  const buckets:[string,number][]= [...periods].sort((a,b)=>a[0]-b[0]).map(([day,v])=>[new Date(day*86400000).toISOString().slice(0,10),v]);
  return <><PageTitle title="Sales" description="Confirmed sales, matched to acquisition lots in FIFO order." />
    <Section title="Sales ledger" action={<button onClick={data.retry}>Refresh</button>}>
      <div className="fields panel-body">
        <Field label="From (UTC)"><input type="date" value={from} onChange={e=>setFrom(e.target.value)}/></Field>
        <Field label="Through (UTC)"><input type="date" value={until} onChange={e=>setUntil(e.target.value)}/></Field>
        <Field label="Item name or ID"><input type="search" value={search} onChange={e=>setSearch(e.target.value)}/></Field>
        <Field label="Retainer"><select value={retainer} onChange={e=>setRetainer(e.target.value)}><option value="">All</option>
          {[...new Set(data.data?.map(s=>s.retainer).filter(Boolean))].map(v=><option key={v} value={v!}>{v}</option>)}</select></Field></div>
      {sources.data?.filter(s => s.found && s.warning).map(s => <Notice tone="warn" key={s.name}>{s.warning}</Notice>)}
      {!data.data ? <ResourceState error={data.error} retry={data.retry} loading="Loading sales…" /> :
        !rows.length ? <Notice>No confirmed sales match these filters.</Notice> : <>
        <dl className="metric-grid panel-body">{[["Realised profit (known units)",gil(profit)],["Net turnover",gil(totalNet)],
          ["Unknown-cost units",gil(unknown)],["Average listing days",number(timed ? duration/timed : null)],
          ["Best item by known profit",ranked[0]?.name ?? "—"],["Worst item by known profit",ranked.at(-1)?.name ?? "—"]]
          .map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        <div className="form-actions"><label><input type="checkbox" checked={weekly} onChange={e=>setWeekly(e.target.checked)} /> Weekly profit</label>
          <button aria-pressed={rollup} onClick={()=>setRollup(!rollup)}>{rollup ? "Show each sale" : "Group by item"}</button>
          <button onClick={()=>exportSales(rows)}>Export sales CSV</button></div>
        <div className="sales-charts"><Bars title={"Realised profit per "+(weekly?"week":"day")+" (UTC)"} buckets={buckets}/>
          <Bars title="Days from listing to sale" buckets={distribution.map((v,i)=>[["<1 day","1–3","3–7","7+ days"][i],v])}/></div>
        {rollup ? <Table label="Sales by item"><thead><tr>{["Item","Sales","Units","Net turnover","Known profit","Gil per listing-slot-day"].map(h=><th key={h}>{h}</th>)}</tr></thead>
          <tbody>{items.map((g,i)=><tr key={i}><td>{g.name}</td><td>{g.count}</td><td>{g.qty}</td><td>{gil(g.net)}</td><td>{g.known ? gil(g.profit) : "Unknown"}</td>
            <td>{gil(g.slotDays ? g.timedProfit/g.slotDays : null)}</td></tr>)}</tbody></Table> :
          <Table className="sales-table" label="Confirmed sales"><thead><tr>{["Item / sale","Qty","FIFO cost/unit","Sold per unit","Realised profit","Days to sale","Listing history"].map(h=><th key={h}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((s,i)=><tr key={i}>
            <td><strong>{s.item.name} {quality(s)}</strong><br/>{time(s.ts)}<br/>{s.retainer ?? "Unknown retainer"}</td>
            <td>{s.qty}</td><td>{s.buy_ppu==null ? "Unknown" : gil(s.buy_ppu)}</td>
            <td>{gil(s.net_ppu)} net<br/><small>{gil(s.gross_ppu)} gross</small></td>
            <td><strong className={s.profit==null ? undefined : s.profit>=0 ? "profit" : "loss"}>{s.profit==null ? "Unknown" : gil(s.profit)}</strong>
              <br/><small>{s.roi_pct==null ? "ROI —" : number(s.roi_pct)+"% ROI"}</small></td>
            <td>{number(s.listing_days)} listed<br/><small>{number(s.purchase_days)} held¹</small></td>
            <td><details><summary>{s.reprices ?? "—"} reprices · {s.own_undercuts ?? "—"} own undercuts</summary>
              <p>Rivals observed below · {rivals(s) || "—"}</p><p>Net stack · {gil(s.net)} gil</p>
              <p>Sources · {s.sources.join(", ")}</p></details></td>
          </tr>)}</tbody></Table>}
        <p className="panel-body"><small>¹ Days held is weighted by units from each purchase. Slot-day returns use sales with known cost and listing duration.</small></p>
        </>}
    </Section></>;
}
