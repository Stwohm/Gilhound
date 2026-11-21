import { useState } from "react";
import type { FormEvent } from "react";
import { api, mockMode } from "../api/client";
import { demoRetainer } from "../api/mock";
import {
  Badge, Field, Item, Loading, Notice, PageTitle, Section, Table, age, ageTone, errorMessage, gil, number,
} from "../common";
import type { Schema, Settings } from "../common";

export default function Retainer({ settings }: { settings: Settings }) {
  const [profiles,setProfiles] = useState<Record<string,Schema<"RetainerScanParams">>>({});
  const [profile,setProfile] = useState("");
  const [name, setName] = useState(mockMode ? demoRetainer : "");
  const [world, setWorld] = useState<string>();
  const [region, setRegion] = useState<string>();
  const [result, setResult] = useState<Schema<"RetainerScanResult">>();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  async function run(event: FormEvent) {
    event.preventDefault();
    setError("");
    setRunning(true);
    try {
      const retainer_name = name.trim();
      if (!retainer_name) throw new Error("Enter a retainer name.");
      setResult(await api.retainerScan({
        retainer_name,
        world: (world ?? settings.home_world).trim() || null,
        buy_region: (region ?? settings.buy_region).trim() || null,
      }, profile || undefined));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setRunning(false);
    }
  }

  return <>
    <PageTitle title="Retainer scan" description="Compare a retainer’s current listings with the cheapest prices in a buy region." />
    <Section title="Find a retainer">
      <form className="panel-body" onSubmit={run}>
        <fieldset disabled={running}>
          <legend className="sr-only">Retainer scan parameters</legend>
          <div className="fields">
            <Field label="Saved profile"><select value={profile} onFocus={()=>void api.retainerProfiles().then(setProfiles).catch(e=>setError(errorMessage(e)))} onChange={e=>{
              const selected=e.target.value; setProfile(selected); const p=profiles[selected];
              if(p){setName(p.retainer_name);setWorld(p.world ?? undefined);setRegion(p.buy_region ?? undefined);}
            }}><option value="">Custom</option>{Object.keys(profiles).map(n=><option key={n}>{n}</option>)}</select></Field>
            <Field label="Retainer name"><input required autoComplete="off" value={name} onChange={(e) => { setName(e.target.value); setProfile(""); }} /></Field>
            <Field label="World"><input value={world ?? settings.home_world} onChange={(e) => { setWorld(e.target.value); setProfile(""); }} /></Field>
            <Field label="Buy region"><input value={region ?? settings.buy_region} onChange={(e) => { setRegion(e.target.value); setProfile(""); }} /></Field>
          </div>
          <div className="form-actions"><button className="primary" type="submit">{running ? "Scanning…" : "Scan retainer"}</button>
            {running && <Loading>Checking uploaded listings across the world…</Loading>}</div>
        </fieldset>
      </form>
    </Section>
    {error && <Notice tone="error">{error}</Notice>}
    <RetainerResults result={result} running={running}/></>;
}

export function RetainerResults({result,running=false}: {result?: Schema<"RetainerScanResult">;running?: boolean}) {
  return <Section title="Retainer listings" detail={result ? `${result.params.retainer_name} · ${number(result.items_scanned)} items checked in ${number(result.duration_s)} s` : undefined}>
      {result?.warnings?.map((warning, index) => <Notice tone="warn" key={index}>{warning}</Notice>)}
      {result?.rows.length ? <Table label="Retainer listings"><thead><tr>
        {["Item", "Listed price", "Qty", "Data age", "Cheapest world / DC", "Cheapest price", "Cheapest qty", "Gap", "Profit / unit"].map((label) => <th key={label} scope="col">{label}</th>)}
      </tr></thead><tbody>
        {result.rows.map((row, index) => <tr key={index}>
          <td><Item buyWorld={row.cheapest_world} item={row.item} /></td><td>{gil(row.their_ppu)}</td><td>{number(row.qty)}</td>
          <td><Badge tone={ageTone(row.data_age_h)}>{age(row.data_age_h)}</Badge></td>
          <td>{row.cheapest_world ?? "—"} {row.cheapest_dc && <small>{row.cheapest_dc}</small>}</td>
          <td>{gil(row.cheapest_ppu)}</td><td>{number(row.cheapest_qty)}</td>
          <td>{row.gap_pct == null ? "—" : `${number(row.gap_pct)}%`}</td>
          <td><strong className={row.profit_per_unit == null ? "" : row.profit_per_unit >= 0 ? "profit" : "loss"}>{gil(row.profit_per_unit)}</strong></td>
        </tr>)}
      </tbody></Table> : <Notice>{result ? "No uploaded listings found for this retainer and world." : running ? "Waiting for retainer listings…" : "Run a retainer scan to compare listings here."}</Notice>}
    </Section>;
}
