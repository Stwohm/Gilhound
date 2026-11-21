import { useCallback, useRef, useState } from "react";
import { api } from "../api/client";
import ProfileSummary from "./ProfileSummary";
import type { Library } from "../api/client";
import { Field, Notice, ResourceState, TravelEditor, errorMessage, travelCosts, travelEntries, useResource } from "../common";
import type { Schema } from "../common";

type Params = Schema<"ScanParams">;
type Profile = Schema<"Profile">;
type Change = Schema<"ProfileChange">;
type Window = Schema<"CandidateWindow">;
type Rule = Schema<"SnipeRule">;
type Props = { library: Library; running?: boolean; onRun?: (params: Params, name: string) => Promise<void> };

function Amount({ label, value, set, min = 0, max, decimal, optional }: {
  label: string; value: number | null | undefined; set: (n: number | null) => void;
  min?: number; max?: number; decimal?: boolean; optional?: boolean;
}) {
  const [input, setInput] = useState<string>();
  const [error, setError] = useState("");
  return <Field label={label}><input type="text" inputMode={decimal ? "decimal" : "numeric"}
    required={!optional} value={input ?? value?.toLocaleString("en-US", { maximumFractionDigits: 12 }) ?? ""}
    placeholder={optional ? "Unlimited" : undefined} aria-invalid={!!error}
    onFocus={() => setInput(value == null ? "" : String(value))}
    onBlur={() => { if (!error) setInput(undefined); }}
    onChange={e => {
      const raw = e.target.value;
      const n = Number(raw.replaceAll(",", ""));
      const valid = raw.trim() === "" ? optional : Number.isFinite(n) && n >= min &&
        (max == null || n <= max) && (decimal || Number.isInteger(n));
      const message = valid ? "" : "Enter " + (decimal ? "a number" : "a whole number") +
        " from " + min + (max == null ? " or higher." : " to " + max + ".");
      setInput(raw); setError(message); e.target.setCustomValidity(message);
      if (valid) set(raw.trim() === "" ? null : n);
    }} />{error && <small role="alert">{error}</small>}</Field>;
}

export default function ProfileEditor(props: Props) {
  const load = useCallback(() => api.getProfiles(props.library), [props.library]);
  const resource = useResource(load);
  const [selected, setSelected] = useState("");
  const [revision, setRevision] = useState(0);
  if (!resource.data) return <ResourceState error={resource.error} retry={resource.retry} loading="Loading profiles…" />;
  const catalog = resource.data;
  const name = Object.hasOwn(catalog.profiles, selected) ? selected : catalog.active;
  return <Editor key={revision + "/" + name} {...props} catalog={catalog} name={name}
    select={setSelected} updated={(next, target) => {
      resource.setData(next); setSelected(target); setRevision(r => r + 1);
    }} />;
}

function Editor({ library, running, onRun, catalog, name, select, updated }: Props & {
  catalog: Schema<"ProfileList">; name: string; select: (name: string) => void;
  updated: (catalog: Schema<"ProfileList">, target: string) => void;
}) {
  const saved = catalog.profiles[name];
  const [mode, setMode] = useState<"view" | "edit" | "create" | "rename">("view");
  const [draft, setDraft] = useState(saved);
  const [travel, setTravel] = useState(travelEntries(saved.params.travel_cost));
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const params = draft.params;
  const windows = params.windows ?? [];
  const tiers = params.freshness_tiers ?? [];
  const dirty = JSON.stringify(saved) !== JSON.stringify(draft) ||
    JSON.stringify(travel) !== JSON.stringify(travelEntries(saved.params.travel_cost));
  function param<K extends keyof Params>(key: K, value: Params[K]) {
    setDraft(d => ({ ...d, params: { ...d.params, [key]: value } }));
  }
  function amount(key: keyof Params, label: string, min = 0, max?: number, decimal = false, optional = false) {
    return <Amount key={key} label={label} value={params[key] as number | null} min={min} max={max}
      decimal={decimal} optional={optional} set={v => param(key, v)} />;
  }
  function ruleField(key: keyof Rule, label: string, min = 0, max?: number, decimal = false) {
    return <Amount key={key} label={label} value={params.snipe_rule?.[key]} min={min} max={max} decimal={decimal}
      set={v => param("snipe_rule", { ...params.snipe_rule!, [key]: v })} />;
  }
  function windowValue(i: number, key: keyof Window, label: string, min = 0) {
    return <Amount label={label} value={windows[i][key]} min={min}
      set={v => param("windows", windows.map((w, j) => j === i ? { ...w, [key]: v } : w))} />;
  }
  async function act(action: Change["action"] | "run", target = name) {
    setError("");
    if ((action === "reset" || action === "delete") && !window.confirm(
      action === "reset" ? "Restore shipped defaults for this profile?" : "Delete this profile?")) return;
    try {
      let profile: Profile | undefined;
      if (["create", "save", "run"].includes(action)) {
        if (!form.current?.reportValidity()) return;
        profile = { ...draft, params: { ...params, travel_cost: travelCosts(travel) } };
        const names = tiers.map(t => t.name.trim().toLowerCase());
        if (names.includes("") || new Set(names).size !== names.length ||
          new Set(tiers.map(t => t.max_age_h)).size !== tiers.length)
          throw new Error("Each tier needs a unique name and age.");
      }
      setBusy(true);
      if (action === "run") { await onRun?.(profile!.params, name); return; }
      const next = await api.changeProfile(library, { action, profile,
        name: action === "create" ? newName.trim() : target, new_name: newName.trim() });
      updated(next, action === "delete" ? next.active : ["create", "rename"].includes(action) ? newName.trim() : target);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <form ref={form} className="panel-body profile-editor" onKeyDown={e => {
    if (e.ctrlKey && e.key === "Enter" && onRun) { e.preventDefault(); if (!e.repeat && !busy && !running) void act("run"); }
  }} onSubmit={e => {
    e.preventDefault();
    if (mode !== "view" || onRun) void act(mode === "view" ? "run" : mode === "edit" ? "save" : mode);
  }}>
    <fieldset disabled={busy || running} className="editor">
      <div className="profile-chips" role="group" aria-label={library === "snipes" ? "Snipe presets" : "Scan profiles"}>
        {[...Object.keys(catalog.defaults), ...Object.keys(catalog.profiles).filter(n => !Object.hasOwn(catalog.defaults, n))].map(n =>
          <button type="button" className="preset" key={n} aria-pressed={name === n} onClick={() => {
            if (n === name || (mode !== "view" && !window.confirm("Discard unsaved changes?"))) return;
            if (library === "snipes") void act("activate", n); else select(n);
          }}>{n}</button>)}
        <button type="button" disabled={mode !== "view"} onClick={() => {
          const initial = Object.values(catalog.defaults).find(p => p.params.preset === params.preset)!;
          setDraft(initial); setTravel(travelEntries(initial.params.travel_cost)); setMode("create");
        }}>+ New</button>
      </div>
      {mode === "view" && <><ProfileSummary name={name} catalog={catalog} />
        <div className="form-actions">
          <button className="edit-profile" type="button" onClick={() => setMode("edit")}>Edit</button>
          <button type="button" onClick={() => { setNewName(name + " copy"); setMode("create"); }}>Duplicate</button>
          <button type="button" onClick={() => void act("reset")}>Reset to default</button>
          {!Object.hasOwn(catalog.defaults, name) && <>
            <button type="button" onClick={() => { setNewName(name); setMode("rename"); }}>Rename</button>
            <button type="button" onClick={() => void act("delete")}>Delete</button></>}
          {onRun && <button title="Run scan (Ctrl+Enter)" className="primary" type="submit">{running ? "Scanning…" : "Run scan"}</button>}
        </div></>}
      {(mode === "create" || mode === "rename") && <Field label="Profile name"><input autoFocus required maxLength={80}
        value={newName} onChange={e => setNewName(e.target.value)} /></Field>}
      {(mode === "edit" || mode === "create") && <>
      <details onInvalidCapture={e => { e.currentTarget.open = true; }}><summary>Candidate search</summary><div className="fields">
        <Field label="Home world"><input required pattern=".*\S.*" value={params.home_world} onChange={e => param("home_world", e.target.value)} /></Field>
        <Field label="Buy region"><input required pattern=".*\S.*" value={params.buy_region} onChange={e => param("buy_region", e.target.value)} /></Field>
        <Field label="Quality"><select value={params.quality} onChange={e => param("quality", e.target.value as Params["quality"])}>
          <option value="both">HQ and NQ</option><option value="hq">HQ only</option><option value="nq">NQ only</option></select></Field>
        {amount("min_stack_size", "Minimum stack size", 1)}
        {amount("home_listings", "Home listings to fetch", 1, 100)}
        {amount("region_listings", "Regional listings to fetch", 1, 100)}
        {params.preset !== "flip" && amount("history_entries", "Seven-day history entries to fetch", 1)}
        {amount("home_entries", "Home sale entries to fetch", 0, 100)}
        {amount("result_limit", "Maximum results", 1, undefined, false, true)}
        <Field label="Saddlebag filter IDs (comma-separated)"><input pattern="[0-9]+(, *[0-9]+)*" required
          defaultValue={params.filters?.join(", ")} onChange={e => { if (e.target.validity.valid)
            param("filters", e.target.value.split(",").map(Number)); }} /></Field>
        {(["region_wide", "include_vendor", "show_out_stock"] as const).map((key, i) =>
          <label key={key}><input type="checkbox" checked={params[key]} onChange={e => param(key, e.target.checked)} />
          {["Search region-wide", "Include vendor items", "Include out-of-stock items"][i]}</label>)}
      </div>
      {windows.map((_, i) => <fieldset key={i}><legend>Search window {i + 1}</legend><div className="fields">
        {windowValue(i, "preferred_roi", "Preferred ROI (%)")}
        {windowValue(i, "min_profit_amount", "Minimum candidate profit (gil)")}
        {windowValue(i, "min_desired_avg_ppu", "Minimum average price (gil)")}
        {windowValue(i, "hours_ago", "Lookback (hours)", 1)}
        {windowValue(i, "min_sales", "Minimum sales")}
        <button type="button" disabled={windows.length === 1} onClick={() => param("windows", windows.filter((_, j) => i !== j))}>Remove window</button>
      </div></fieldset>)}
      <button type="button" onClick={() => param("windows", [...windows, { ...windows[0] }])}>Add search window</button></details>
      <details onInvalidCapture={e => { e.currentTarget.open = true; }}><summary>Volume proof</summary><div className="fields">
        {amount("min_history_sales", "Minimum sales in seven days", 1)}
        {amount("min_distinct_buyers", "Minimum distinct buyers")}
        {amount("min_days_with_sales", "Minimum days with sales", 0, 7)}
        {amount("min_units_day", "Minimum units per day", 0, undefined, true)}
        {amount("max_top_buyer_share", "Maximum top-buyer fraction", 0, 1, true)}
        {amount("max_data_age_h", "Maximum listing age (hours)", 0.000001, undefined, true, true)}
        {amount("max_sale_age_h", "Maximum last sale age (hours)", 0.000001, undefined, true, true)}
      </div></details>
      <details onInvalidCapture={e => { e.currentTarget.open = true; }}><summary>Profit model</summary><div className="fields">
        {amount("min_ppu", "Minimum purchase price (gil)")}
        {params.preset === "snipe" && amount("undercut_gil", "Undercut lowest listing by (gil)")}
        {amount("min_profit", "Minimum net profit per unit (gil)")}
        {amount("buyer_tax", "Buyer tax fraction", 0, 1, true)}
        {amount("seller_tax", "Seller tax fraction", 0, 1, true)}
        {amount("sellable_days", "Sellable days", 0.000001, undefined, true)}
      </div><h4>Travel cost by data centre</h4><TravelEditor entries={travel} onChange={setTravel} /></details>
      {params.preset === "snipe" && <><fieldset><legend>Alert rules</legend><div className="fields">
        {ruleField("max_home_median_ratio", "Maximum fraction of home median", 0, 1, true)}
        {ruleField("min_profit", "Minimum alert profit (gil)")}
        {ruleField("min_distinct_buyers_7d", "Minimum unique buyers")}
        {ruleField("max_last_sale_age_h", "Latest sale within (hours)", 0.000001, undefined, true)}
        {ruleField("max_top_buyer_share", "Maximum buyer fraction", 0, 1, true)}
      </div></fieldset>
      <fieldset><legend>Freshness tiers</legend>{tiers.map((t, i) => {
        const update = (change: Partial<typeof t>) => param("freshness_tiers", tiers.map((v, j) => j === i ? { ...v, ...change } : v));
        return <div className="fields" key={i}><Field label="Tier name"><input required value={t.name} onChange={e => update({ name: e.target.value })} /></Field>
          <Amount label="Younger than (hours)" min={0.000001} decimal value={t.max_age_h} set={v => update({ max_age_h: v! })} />
          <label><input type="checkbox" checked={t.send_discord} onChange={e => update({ send_discord: e.target.checked })} />Send to Discord</label>
          <button type="button" onClick={() => param("freshness_tiers", tiers.filter((_, j) => i !== j))}>Remove tier</button></div>;
      })}<button type="button" onClick={() => param("freshness_tiers", [...tiers, {
        name: "", max_age_h: Math.max(0, ...tiers.map(t => t.max_age_h)) + 1, send_discord: false,
      }])}>Add tier</button></fieldset></>}
      {dirty && <small role="status">Unsaved changes</small>}</>}
      {mode !== "view" && <div className="form-actions"><button className="primary" type="submit">Save</button>
        <button type="button" onClick={() => updated(catalog, name)}>Cancel</button></div>}
      {error && <Notice tone="error">{error}</Notice>}
    </fieldset>
  </form>;
}
