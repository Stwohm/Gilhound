import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, mockMode } from "../api/client";
import {
  Badge, Field, Notice, Numeric, PageTitle, ResourceState, Section, TravelEditor, errorMessage, travelCosts,
  travelEntries, useResource,
} from "../common";
import type { Schema, Settings, TravelEntry } from "../common";

type Tier = Schema<"FreshnessTier">;
type ActionResult = { success: boolean; message: string };

function SettingsForm({ settings, onSaved }: { settings: Settings; onSaved: (settings: Settings) => void }) {
  const { webhook_url: savedWebhook, ...editable } = settings;
  const [draft, setDraft] = useState(editable);
  const [travel, setTravel] = useState<TravelEntry[]>(() => travelEntries(settings.travel_cost));
  const [tiers, setTiers] = useState<Tier[]>(settings.freshness_tiers ?? []);
  const [clearWebhook, setClearWebhook] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testRunning, setTestRunning] = useState(false);
  const [saveResult, setSaveResult] = useState<ActionResult>();
  const [testResult, setTestResult] = useState<ActionResult>();
  const webhook = useRef<HTMLInputElement>(null);

  function update<K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function editTier(index: number, entry: Tier) {
    setTiers((current) => current.map((tier, i) => i === index ? entry : tier));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaveResult(undefined);
    try {
      const home_world = draft.home_world.trim();
      const buy_region = draft.buy_region.trim();
      if (!home_world || !buy_region) throw new Error("Enter a home world and buy region.");
      const ordered = tiers.map((tier) => ({ ...tier, name: tier.name.trim() })).sort((a, b) => a.max_age_h - b.max_age_h);
      if (ordered.some((tier, i) => !tier.name || tier.max_age_h === ordered[i - 1]?.max_age_h) ||
        new Set(ordered.map((tier) => tier.name.toLowerCase())).size !== ordered.length) {
        throw new Error("Each freshness tier needs a unique name and maximum age.");
      }
      const url = webhook.current?.value.trim();
      const next = await api.saveSettings({
        ...draft, home_world, buy_region, freshness_tiers: ordered, travel_cost: travelCosts(travel),
        ...(clearWebhook ? { webhook_url: null } : url ? { webhook_url: url } : {}),
      });
      if (webhook.current) webhook.current.value = "";
      setClearWebhook(false);
      const { webhook_url: _mask, ...fields } = next;
      setDraft(fields);
      setTravel(travelEntries(next.travel_cost));
      setTiers(next.freshness_tiers ?? []);
      onSaved(next);
      setSaveResult({ success: true, message: "Settings saved." });
    } catch (error) {
      setSaveResult({ success: false, message: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  async function testDiscord() {
    setTestRunning(true);
    setTestResult(undefined);
    try {
      await api.testDiscord();
      setTestResult({ success: true, message: mockMode ? "Discord test simulated in mock mode." : "Discord test sent." });
    } catch (error) {
      setTestResult({ success: false, message: errorMessage(error) });
    } finally {
      setTestRunning(false);
    }
  }

  return <form className="stack" onSubmit={save}>
    <Section title="Market defaults">
      <fieldset className="panel-body" disabled={saving}>
        <legend className="sr-only">Market defaults</legend>
        <div className="fields">
          <Field label="Home world"><input required value={draft.home_world} onChange={(e) => update("home_world", e.target.value)} /></Field>
          <Field label="Buy region"><input required value={draft.buy_region} onChange={(e) => update("buy_region", e.target.value)} /></Field>
          <Field label="Scan interval (min)"><Numeric min={1} value={draft.scan_interval_min ?? 5} onValue={(value) => update("scan_interval_min", value)} /></Field>
          {draft.snipe_rule && <Field label="Maximum top buyer share (%)">
            <Numeric max={100} step="any" value={draft.snipe_rule.max_top_buyer_share * 100}
              onValue={(value) => update("snipe_rule", { ...draft.snipe_rule!, max_top_buyer_share: value / 100 })} />
          </Field>}
        </div>
      </fieldset>
    </Section>
    <Section title="Freshness tiers" detail="The first tier whose age limit exceeds a listing’s data age applies.">
      <fieldset className="panel-body editor" disabled={saving}>
        <legend className="sr-only">Freshness tiers</legend>
        {tiers.length === 0 && <p className="subtle">No freshness tiers configured.</p>}
        {tiers.map((tier, index) => <div className="editor-row tier-row" key={index}>
          <Field label="Name"><input required value={tier.name} onChange={(e) => editTier(index, { ...tier, name: e.target.value })} /></Field>
          <Field label="Max age (h)"><Numeric min={0.01} step="any" value={tier.max_age_h} onValue={(value) => editTier(index, { ...tier, max_age_h: value })} /></Field>
          <label className="check"><input type="checkbox" checked={tier.send_discord} onChange={(e) => editTier(index, { ...tier, send_discord: e.target.checked })} />Send to Discord</label>
          <button type="button" onClick={() => setTiers(tiers.filter((_, i) => i !== index))} aria-label={`Remove freshness tier ${index + 1}`}>Remove</button>
        </div>)}
        <button type="button" onClick={() => setTiers([...tiers, {
          name: "", max_age_h: Math.max(0, ...tiers.map((tier) => tier.max_age_h || 0)) + 1, send_discord: false,
        }])}>+ Add tier</button>
      </fieldset>
    </Section>
    <Section title="Discord notifications">
      <fieldset className="panel-body editor" disabled={saving}>
        <legend className="sr-only">Discord notifications</legend>
        <label className="check"><input type="checkbox" checked={draft.discord_notifier ?? false} onChange={(e) => update("discord_notifier", e.target.checked)} />Enable Discord notifier</label>
        <div className="fields">
          <Field label="Discord webhook URL">
            <input type="password" ref={webhook} disabled={clearWebhook} autoComplete="new-password" spellCheck={false}
              placeholder={savedWebhook ? "Saved · enter a new URL to replace" : "Enter webhook URL"} />
          </Field>
          <Field label="Dedup cooldown (h)"><Numeric step="any" value={draft.dedup_cooldown_h ?? 6} onValue={(value) => update("dedup_cooldown_h", value)} /></Field>
        </div>
        {savedWebhook && <label className="check"><input type="checkbox" checked={clearWebhook} onChange={(e) => setClearWebhook(e.target.checked)} />Remove saved webhook on save</label>}
        <button type="button" disabled={testRunning} onClick={() => void testDiscord()}>{testRunning ? "Testing…" : "Test Discord"}</button>
        {testResult && <Notice tone={testResult.success ? "good" : "error"}>{testResult.message}</Notice>}
      </fieldset>
    </Section>
    <Section title="Travel costs" detail="Per-unit gil cost applied when buying from each data centre.">
      <fieldset className="panel-body" disabled={saving}>
        <legend className="sr-only">Travel costs</legend>
        <TravelEditor entries={travel} onChange={setTravel} />
      </fieldset>
    </Section>
    <div className="save-bar">
      <button className="primary" type="submit" disabled={saving || testRunning}>{saving ? "Saving…" : "Save settings"}</button>
      {saveResult && <Notice tone={saveResult.success ? "good" : "error"}>{saveResult.message}</Notice>}
    </div>
  </form>;
}

export default function SettingsScreen({ resource }: { resource: ReturnType<typeof useResource<Settings>> }) {
  const info = useResource(api.getInfo);
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<ActionResult>();

  async function exportDiagnostics() {
    setExporting(true);
    setExportResult(undefined);
    try {
      const archive = await api.exportDiagnostics();
      const url = URL.createObjectURL(archive);
      const link = document.createElement("a");
      link.href = url;
      link.download = "gilhound-diagnostics.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setExportResult({ success: true, message: "Diagnostics download started." });
    } catch (error) {
      setExportResult({ success: false, message: errorMessage(error) });
    } finally {
      setExporting(false);
    }
  }

  return <>
    <PageTitle title="Settings" description="Manage market defaults, freshness tiers and Discord notifications." />
    {resource.data ? <SettingsForm settings={resource.data} onSaved={resource.setData} />
      : <Section title="Preferences"><ResourceState error={resource.error} retry={resource.retry} loading="Loading settings…" /></Section>}
    <Section title="About & diagnostics" action={info.data && <Badge>v{info.data.version}</Badge>}>
      {!info.data && <ResourceState error={info.error} retry={info.retry} loading="Loading app version…" />}
      <div className="panel-body editor">
        <button type="button" disabled={exporting} onClick={() => void exportDiagnostics()}>{exporting ? "Exporting…" : "Export diagnostics"}</button>
        {exportResult && <Notice tone={exportResult.success ? "good" : "error"}>{exportResult.message}</Notice>}
      </div>
    </Section>
  </>;
}
