import { useEffect, useRef, useState } from "react";
import ProfileEditor from "./ProfileEditor";
import { PartnerBadge, usePartnerFilter, visiblePartnerRow } from "../Partner";
import { api } from "../api/client";
import {
  Badge, Item, Loading, Notice, PageTitle, Section, Table, ResourceState, useResource, age, ageTone, epoch, errorMessage, gil, time,
} from "../common";
import type { Schema, Settings } from "../common";

type Hit = Schema<"SnipeHit">;
const sessionHistory = new Map<string, Hit>();

export default function Snipes({ resource }: { resource: ReturnType<typeof useResource<Settings>> }) {
  const {hide,control} = usePartnerFilter();
  const settings = resource.data;
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<Schema<"SnipeStatus">>();
  const [statusError, setStatusError] = useState("");
  const [now, setNow] = useState(epoch);

  const [hits, setHits] = useState<Hit[]>();
  const [history, setHistory] = useState(() => [...sessionHistory.values()]);
  const [lastChecked, setLastChecked] = useState<number>();
  const [pollError, setPollError] = useState("");
  const [runError, setRunError] = useState("");
  const [running, setRunning] = useState(false);
  const manual = useRef(false);
  const generation = useRef(0);
  const active = useRef(false);

  function quote(hit: Hit) {
    return {...hit, plannedStacks: [hit.buy_qty_at_price],
      maxAge: settings?.freshness_tiers?.[0]?.max_age_h ?? 1,
      home: hit.item.universalis_url ? new URL(hit.item.universalis_url).searchParams.get("server") ?? undefined : undefined};
  }

  function receive(results: Hit[]) {
    setHits(results);
    setLastChecked(epoch());
    for (const hit of results) sessionHistory.set(hit.listing_key, hit);
    setHistory([...sessionHistory.values()]);
  }

  useEffect(() => {
    active.current = true;
    let disposed = false;
    let polling = false;
    async function poll() {
      if (manual.current || polling) return;
      polling = true;
      const request = ++generation.current;
      try {
        const results = await api.getSnipes();
        if (!disposed && request === generation.current) {
          receive(results);
          setPollError("");
        }
      } catch (failure) {
        if (!disposed && request === generation.current) setPollError(errorMessage(failure));
      } finally {
        polling = false;
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 15_000);
    return () => {
      disposed = true;
      active.current = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number;
    async function refresh() {
      try {
        const next = await api.getSnipeStatus();
        if (!disposed) { setStatus(next); setStatusError(""); }
      } catch (error) {
        if (!disposed) setStatusError(errorMessage(error));
      } finally {
        if (!disposed) timer = window.setTimeout(refresh, 2000);
      }
    }
    void refresh();
    const clock = window.setInterval(() => setNow(epoch()), 1000);
    return () => { disposed = true; clearTimeout(timer); clearInterval(clock); };
  }, [settings, running]);

  async function save(change: Partial<Settings>) {
    if (!settings || saving) return;
    setSaving(true);
    setRunError("");
    try {
      resource.setData(await api.saveSettings(change));
    } catch (error) {
      setRunError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  const remaining = Math.max(0, (status?.next_run_ts ?? now) - now);
  const countdown = Math.floor(remaining / 60) + ":" + String(remaining % 60).padStart(2, "0");
  const state = running || status?.running ? "Running…" : settings?.auto_snipe_enabled === false
    ? "Paused" : status?.next_run_ts ? "Next run in " + countdown : "Waiting for schedule…";

  async function run() {
    manual.current = true;
    generation.current++;
    setRunning(true);
    setRunError("");
    try {
      const results = await api.runSnipes();
      if (active.current) {
        receive(results);
        setPollError("");
      }
    } catch (failure) {
      if (active.current) setRunError(errorMessage(failure));
    } finally {
      manual.current = false;
      if (active.current) setRunning(false);
    }
  }

  return <>
    <PageTitle title="Snipes" description="Track discounted listings and their Discord notification status.">
      <button className="primary" type="button" disabled={running || status?.running} onClick={() => void run()}>{running ? "Scanning…" : "Run now"}</button>
    </PageTitle>
    {settings ? <section className="panel panel-body" aria-label="Snipe controls">
      <fieldset className="snipe-controls" disabled={saving}>
        <label className="check"><input type="checkbox" role="switch" checked={settings.auto_snipe_enabled ?? true}
          onChange={e => void save({ auto_snipe_enabled: e.target.checked })} />Auto snipe</label>
        <label>Interval (min) <input key={String(settings.scan_interval_min) + saving} type="number" min="1" max="240" required
          defaultValue={settings.scan_interval_min} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }}
          onBlur={e => {
            const input = e.currentTarget;
            if (!input.reportValidity()) { input.value = String(settings.scan_interval_min); return; }
            if (input.valueAsNumber !== settings.scan_interval_min) void save({ scan_interval_min: input.valueAsNumber });
          }} /></label>
        <label className="check"><input type="checkbox" role="switch" checked={Boolean(settings.webhook_url && settings.discord_notifier)}
          disabled={!settings.webhook_url} aria-describedby={!settings.webhook_url ? "webhook-hint" : undefined}
          onChange={e => void save({ discord_notifier: e.target.checked })} />Send to Discord</label>
        {!settings.webhook_url && <small id="webhook-hint">Set a webhook in Settings</small>}
      </fieldset>
      <small>{state} · {status?.last_run_ts
        ? `Last run ${time(status.last_run_ts)} · ${status.fresh} fresh · ${status.stale} stale · ${status.notified} notified`
        : "No completed run yet"}</small>
      {!!settings.partner_retainers?.length && <small>{status?.partner_age_min == null ? "Partner data pending" : `Partner data ${status.partner_age_min} min old`}
        {status?.partner_requested_items != null && ` · ${status.partner_resolved_items} of ${status.partner_requested_items} items resolved`}</small>}
      {statusError && <Notice tone="error">{statusError}</Notice>}
    </section> : <ResourceState error={resource.error} retry={resource.retry} loading="Loading snipe controls…" />}
    <Section title="Snipe rules"><ProfileEditor library="snipes" /></Section>
    {runError && <Notice tone="error">{runError}</Notice>}
    <Section title="Live listings" detail={lastChecked == null ? "Refreshes every 15 seconds" : `Last checked ${time(lastChecked)} · Refreshes every 15 seconds`}>
      {control}
      {pollError && <Notice tone="error">{pollError}</Notice>}
      {hits === undefined && !pollError && <Loading>Loading snipe listings…</Loading>}
      {hits?.length === 0 && <Notice>No current listings match your snipe rules.</Notice>}
      {!!hits?.length && <Table label="Live snipe listings">
        <thead><tr>{["Item", "Buy world / DC", "Buy price", "Qty at price", "Home median", "Profit / unit", "Demand (7d)", "Tier / buy listing last seen", "Discord"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{hits.filter(h => visiblePartnerRow(h, hide)).map((hit) => <tr key={hit.listing_key}>
          <td><PartnerBadge row={hit}/><Item quote={quote(hit)} buyWorld={hit.buy_world} item={hit.item} /></td><td>{hit.buy_world} <small>{hit.buy_dc}</small></td>
          <td>{gil(hit.buy_ppu)}</td><td>{gil(hit.buy_qty_at_price)}</td><td>{gil(hit.home_median_7d)}</td>
          <td><strong className={hit.profit_per_unit >= 0 ? "profit" : "loss"}>{gil(hit.profit_per_unit)}</strong></td>
          <td>{hit.volume_proof.units_per_day.toFixed(1)} units/day · {hit.volume_proof.distinct_buyers_7d} buyers<br/>
            Last home sale {age(hit.volume_proof.last_sale_age_h)} · top buyer {(hit.volume_proof.top_buyer_share*100).toFixed(0)}%</td>
          <td><Badge tone={ageTone(hit.data_age_h)}>{hit.tier.name} · {age(hit.data_age_h)}</Badge></td>
          <td>{hit.notified_ts == null ? <Badge>Not notified</Badge> : <span title={time(hit.notified_ts)}><Badge tone="good">Notified</Badge></span>}</td>
        </tr>)}</tbody>
      </Table>}
    </Section>
    <Section title="Snipe history" detail="Listings observed during this session">
      {history.length ? <Table label="Snipe history">
        <thead><tr>{["Item", "First seen", "Buy world", "Buy price", "Tier / buy listing last seen at scan time", "Last notified"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{history.filter(h => visiblePartnerRow(h, hide)).sort((a, b) => b.first_seen_ts - a.first_seen_ts).map((hit) => <tr key={hit.listing_key}>
          <td><PartnerBadge row={hit}/><Item quote={quote(hit)} buyWorld={hit.buy_world} item={hit.item} /></td><td>{time(hit.first_seen_ts)}</td><td>{hit.buy_world}</td><td>{gil(hit.buy_ppu)}</td>
          <td><Badge tone={ageTone(hit.data_age_h)}>{hit.tier.name} · {age(hit.data_age_h)}</Badge></td>
          <td>{hit.notified_ts == null ? "Not notified" : time(hit.notified_ts)}</td>
        </tr>)}</tbody>
      </Table> : <Notice>No listings observed in this session yet.</Notice>}
    </Section>
  </>;
}
