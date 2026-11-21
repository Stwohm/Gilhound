import { useRef, useState } from "react";
import { json } from "./api/client";
import { Field, Section, errorMessage } from "./common";
import type { Schema, Settings } from "./common";

type Share = Schema<"PartnerShare">;
type Retainer = Schema<"PartnerRetainer">;
type Item = Schema<"AvoidItem">;

export function PartnerBadge({row}: {row: {partner?: string | null; partner_detail?: string | null; you_list?: boolean}}) {
  const primary = row.partner_detail?.split("\n")[0];
  const label = primary && (row.partner === "shared" || row.partner === "now")
    ? primary : `Partner ${row.partner}`;
  return <>{row.partner && <small className="badge" title={row.partner_detail ?? undefined}>🤝 {label}</small>}
    {row.you_list && <small className="badge">You list this</small>}</>;
}

export function visiblePartnerRow(row: {partner?: string | null}, hide: boolean) {
  return !hide || !row.partner;
}

export function usePartnerFilter() {
  const [hide, setHide] = useState(false);
  const control = <label className="check"><input type="checkbox" checked={hide}
    onChange={e => setHide(e.target.checked)} />Hide partner items</label>;
  return {hide, control};
}

function Retainers({label, rows, change}: {label: string; rows: Retainer[]; change: (v: Retainer[]) => void}) {
  return <fieldset><legend>{label}</legend>{rows.map((r,i) => <div className="fields" key={i}>
    <Field label="Retainer name"><input required maxLength={64} value={r.name} onChange={e => change(rows.map((v,j) => j === i ? {...v,name:e.target.value} : v))}/></Field>
    <Field label="World"><input placeholder="Home world" maxLength={64} value={r.world ?? ""} onChange={e => change(rows.map((v,j) => j === i ? {...v,world:e.target.value} : v))}/></Field>
    <button type="button" onClick={() => change(rows.filter((_,j) => i !== j))}>Remove</button></div>)}
    <button type="button" onClick={() => change([...rows,{name:"",world:""}])}>Add retainer</button></fieldset>;
}

const key = (i: Item) => `${i.id}/${i.hq ? "HQ" : "NQ"}`;

function summary(share: Share, staleHours: number) {
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - share.created_ts) / 60));
  const age = minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h`;
  return `${share.listings?.length ?? 0} active listings, ${share.items.length} avoid items, from ${share.sender || "unlabeled partner"}, created ${age} ago${minutes >= staleHours * 60 ? " (stale)" : ""}`;
}

export function PartnerSettings({draft, change, hasWebhook}: {draft: Omit<Settings,"webhook_url">; change: (v: Partial<Settings>) => void; hasWebhook: boolean}) {
  const [code, setCode] = useState("");
  const [preview, setPreview] = useState<Share>();
  const [include, setInclude] = useState(false);
  const [posting, setPosting] = useState(false);
  const inspection = useRef(0);
  const [message, setMessage] = useState("");
  const own = draft.own_avoid ?? [];
  const previous = draft.partner_manual?.items ?? [];
  function edit(i: number, fields: Partial<Item>) {
    change({own_avoid:own.map((v,j) => i === j ? {...v,...fields} : v)});
  }
  async function share(action: "copy" | "file" | "discord") {
    const request = {sender:draft.share_sender ?? "",items:own,retainers:include ? draft.own_retainers ?? [] : []};
    try {
      if (action === "discord") {
        setPosting(true);
        await json("/api/partner/discord","POST",request);
        setMessage("Share code posted to Discord.");
        return;
      }
      const value = await json<string>("/api/partner/share","POST",request);
      inspection.current++;
      setCode(value); setPreview(undefined);
      if (action === "file") {
        const url = URL.createObjectURL(new Blob([value],{type:"text/plain"}));
        const a = document.createElement("a"); a.href = url; a.download = "items.gilhound-share"; a.click();
        setTimeout(() => URL.revokeObjectURL(url),1000);
      } else await navigator.clipboard.writeText(value);
      setMessage(action === "file" ? "Share exported." : "Share code copied.");
    } catch(e) {setMessage(errorMessage(e));} finally {if (action === "discord") setPosting(false);}
  }
  async function inspect(value: string) {
    const id = ++inspection.current;
    setPreview(undefined);
    try {
      const result = await json<Share>("/api/partner/preview","POST",{code:value.trim()});
      if (id === inspection.current) {setPreview(result); setMessage("");}
    } catch(e) {if (id === inspection.current) setMessage(errorMessage(e));}
  }
  return <Section title="Partner"><div className="panel-body stack">
    <p>Universalis depends on player uploads and shows up to 100 offers per item, so your partner’s share code is the reliable source for their active listings.</p>
    <Retainers label="Partner retainers" rows={draft.partner_retainers ?? []} change={v=>change({partner_retainers:v})}/>
    <div className="fields">{([["partner_snapshot_hours","Mark shared listings stale after (hours)",48,8760],["partner_cache_min","Refresh after (minutes)",45,1440],["partner_regular_days","Days seen for regular",5,90],["partner_window_days","History window (days)",14,90]] as const).map(([k,label,initial,max]) =>
      <Field key={k} label={label}><input required type="number" min={1} max={max} value={draft[k] ?? initial} onChange={e=>change({[k]:Number(e.target.value)})}/></Field>)}</div>
    <label><input type="checkbox" checked={draft.suppress_partner_discord ?? true} onChange={e=>change({suppress_partner_discord:e.target.checked})}/> Don't send partner items to Discord</label>
    <fieldset><legend>My items, please avoid</legend>{own.map((item,i)=><div className="fields" key={i}>
      <Field label="Item ID"><input required type="number" min={1} value={item.id || ""} onChange={e=>edit(i,{id:Number(e.target.value)})}/></Field>
      <label><input type="checkbox" checked={item.hq} onChange={e=>edit(i,{hq:e.target.checked})}/> HQ</label>
      <Field label="Note"><input maxLength={160} value={item.note ?? ""} onChange={e=>edit(i,{note:e.target.value})}/></Field>
      <button type="button" onClick={()=>change({own_avoid:own.filter((_,j)=>j!==i)})}>Remove</button></div>)}
      <button type="button" onClick={()=>change({own_avoid:[...own,{id:0,hq:false,note:""}]})}>Add item</button></fieldset>
    <Field label="Sender label"><input maxLength={80} value={draft.share_sender ?? ""} onChange={e=>change({share_sender:e.target.value})}/></Field>
    <Retainers label="My retainers" rows={draft.own_retainers ?? []} change={v=>change({own_retainers:v})}/>
    <label><input type="checkbox" checked={include} onChange={e=>setInclude(e.target.checked)}/> Include my retainers in this share</label>
    <div><button type="button" onClick={()=>void share("copy")}>Copy share code</button> <button type="button" onClick={()=>void share("file")}>Export file</button> <button type="button" disabled={!hasWebhook || posting} onClick={()=>void share("discord")}>Post share code to Discord</button></div>
    {!hasWebhook && <small>Save a Discord webhook in Settings to post your share code.</small>}
    {draft.partner_manual && <details><summary>{summary(draft.partner_manual, draft.partner_snapshot_hours ?? 48)}</summary>{previous.map(i=><p key={key(i)}>{key(i)} {i.note}</p>)}</details>}
    <Field label="Share code"><textarea value={code} maxLength={256000} onChange={e=>{inspection.current++;setCode(e.target.value);setPreview(undefined);}}/></Field>
    <div><button type="button" onClick={()=>void inspect(code)}>Preview import</button>
    <label> Import file <input type="file" accept=".gilhound-share" onChange={async e=>{
      const f=e.target.files?.[0]; if(!f)return; const id=++inspection.current; setPreview(undefined);
      if(f.size>256000){setMessage("File exceeds size limit.");return;}
      const value=await f.text();if(id!==inspection.current)return;setCode(value);await inspect(value);
    }}/></label></div>
    {preview && <div><strong>{summary(preview, draft.partner_snapshot_hours ?? 48)}</strong>
      <p>Add · {preview.items.filter(i=>!previous.some(j=>key(i)===key(j))).map(key).join(", ") || "none"}</p>
      <p>Remove · {previous.filter(i=>!preview.items.some(j=>key(i)===key(j))).map(key).join(", ") || "none"}</p>
      <button type="button" onClick={()=>{change({partner_manual:{...preview,retainers:[]}});setMessage("Snapshot replaced. Save settings to apply.");}}>Replace partner snapshot</button>
      {!!preview.retainers?.length && <button type="button" onClick={()=>{
        const all=[...(draft.partner_retainers ?? []),...(preview.retainers ?? [])];
        change({partner_retainers:[...new Map(all.map(r=>[JSON.stringify([r.name.toLowerCase(),(r.world || draft.home_world).toLowerCase()]),r])).values()]});
      }}>Add shared retainers</button>}</div>}
    {message && <p role="status">{message}</p>}
  </div></Section>;
}
