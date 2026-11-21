import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, mockMode } from "../api/client";
import type { useUpdates } from "../api/useUpdates";
import {
  Badge, Field, Notice, Numeric, PageTitle, ResourceState, Section, TravelEditor, errorMessage, time, travelCosts,
  travelEntries, useResource,
} from "../common";
import type { Settings, TravelEntry } from "../common";

type ActionResult = { success: boolean; message: string };

function SettingsForm({ settings, onSaved }: { settings: Settings; onSaved: (settings: Settings) => void }) {
  const { webhook_url: savedWebhook, ...editable } = settings;
  const [draft, setDraft] = useState(editable);
  const [travel, setTravel] = useState<TravelEntry[]>(() => travelEntries(settings.travel_cost));
  const [clearWebhook, setClearWebhook] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testRunning, setTestRunning] = useState(false);
  const [saveResult, setSaveResult] = useState<ActionResult>();
  const [testResult, setTestResult] = useState<ActionResult>();
  const webhook = useRef<HTMLInputElement>(null);

  function update<K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaveResult(undefined);
    try {
      const home_world = draft.home_world.trim();
      const buy_region = draft.buy_region.trim();
      if (!home_world || !buy_region) throw new Error("Enter a home world and buy region.");
      const url = webhook.current?.value.trim();
      const next = await api.saveSettings({
        ...draft, home_world, buy_region, travel_cost: travelCosts(travel),
        ...(clearWebhook ? { webhook_url: null } : url ? { webhook_url: url } : {}),
      });
      if (webhook.current) webhook.current.value = "";
      setClearWebhook(false);
      const { webhook_url: _mask, ...fields } = next;
      setDraft(fields);
      setTravel(travelEntries(next.travel_cost));
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
          <a href="#snipes/rules">Edit snipe rules and schedule</a>
        </div>
      </fieldset>
    </Section>
    <Section title="Discord notifications">
      <fieldset className="panel-body editor" disabled={saving}>
        <legend className="sr-only">Discord notifications</legend>
        <label className="check"><input type="checkbox" checked={draft.discord_notifier ?? false} onChange={(e) => update("discord_notifier", e.target.checked)} />Enable Discord notifier</label>
        <div className="fields">
          <Field label="Coin emoji"><input required maxLength={80} value={draft.discord_coin_emoji ?? "🪙"} onChange={e => update("discord_coin_emoji", e.target.value)} /></Field>
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
    <Section title="Travel costs" detail="Fixed gil cost per buying trip, shared across the purchased units.">
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

export default function SettingsScreen({ resource, updates }: {
  resource: ReturnType<typeof useResource<Settings>>;
  updates: ReturnType<typeof useUpdates>;
}) {
  const { info, checking, check, result } = updates;
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
    <PageTitle title="Settings" description="Manage market defaults and Discord notifications." />
    {resource.data ? <SettingsForm settings={resource.data} onSaved={resource.setData} />
      : <Section title="Preferences"><ResourceState error={resource.error} retry={resource.retry} loading="Loading settings…" /></Section>}
    <Section title="About / Updates" action={info && <Badge>v{info.version}</Badge>}>
      <div className="panel-body editor">
        <button type="button" disabled={checking} onClick={() => void check()}>Check for updates</button>
        <p>Last checked · {info?.update_checked_at == null ? "Never" : time(info.update_checked_at)}</p>
        <p role="status">{result}</p>
      </div>
    </Section>
    <Section title="Diagnostics">
      <div className="panel-body editor">
        <button type="button" disabled={exporting} onClick={() => void exportDiagnostics()}>{exporting ? "Exporting…" : "Export diagnostics"}</button>
        {exportResult && <Notice tone={exportResult.success ? "good" : "error"}>{exportResult.message}</Notice>}
      </div>
    </Section>
  </>;
}
