import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Field, Notice, PageTitle, Section, Table, errorMessage, number, time } from "../common";
import type { Schema } from "../common";
import { ScanResults } from "./Scan";
import { RetainerResults } from "./Retainer";

type Entry = Schema<"ArchiveEntry">;
function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, v] of Object.entries(value)) {
    const path = prefix + key;
    if (v && typeof v === "object" && !Array.isArray(v))
      Object.assign(out, flatten(v as Record<string, unknown>, path + "."));
    else out[path] = v == null ? "" : Array.isArray(v) ? JSON.stringify(v) : String(v);
  }
  return out;
}
function download(entry: Entry, format: "json" | "csv") {
  let data = JSON.stringify(entry, null, 2);
  if (format === "csv") {
    const rows = entry.result.rows.map((r) => flatten(r));
    const fields = [...new Set(rows.flatMap(Object.keys))];
    const escape = (s: string) => '"' + s.replaceAll('"', '""') + '"';
    data = [fields, ...rows.map((r) => fields.map((k) => r[k] ?? ""))]
      .map((line) => line.map(escape).join(","))
      .join("\r\n");
  }
  const url = URL.createObjectURL(
    new Blob([data], { type: format === "csv" ? "text/csv;charset=utf-8" : "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `gilhound-${entry.kind}-${entry.id}.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function EntryView({ entry, refresh, close }: { entry: Entry; refresh: () => void; close: () => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const result = entry.result;
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setMessage(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Section title={`${entry.profile ?? entry.kind} · ${time(entry.started_ts)}`}>
        <div className="panel-body">
          {entry.description.map((s, i) => (
            <p key={i}>{s}</p>
          ))}
          <details>
            <summary>Full parameter snapshot</summary>
            <pre>{JSON.stringify(result.params, null, 2)}</pre>
          </details>
          <fieldset disabled={busy} className="form-actions">
            <button
              onClick={() =>
                void action(async () => {
                  if ("items_scanned" in result)
                    await api.retainerScan(result.params, entry.profile ?? undefined);
                  else await api.scan(result.params, entry.profile ?? undefined);
                  refresh();
                  setMessage("Run archived.");
                })
              }
            >
              {busy ? "Working…" : "Re-run with these settings"}
            </button>
            <button onClick={() => setSaving(!saving)}>Save these settings as profile</button>
            <button onClick={() => download(entry, "json")}>Export JSON</button>
            <button onClick={() => download(entry, "csv")}>Export CSV</button>
            <button
              onClick={() => {
                if (window.confirm("Delete this archived scan?"))
                  void action(async () => {
                    await api.deleteEntry(entry.id);
                    close();
                    refresh();
                  });
              }}
            >
              Delete
            </button>
          </fieldset>
          {saving && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void action(async () => {
                  if ("items_scanned" in result) await api.saveRetainerProfile(name, result.params);
                  else
                    await api.changeProfile("scan", {
                      action: "create",
                      name,
                      profile: { params: result.params },
                    });
                  setSaving(false);
                  setMessage("Profile saved.");
                });
              }}
            >
              <Field label="Profile name">
                <input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <button disabled={busy}>Save profile</button>
            </form>
          )}
          {message && <Notice>{message}</Notice>}
        </div>
      </Section>
      {"items_scanned" in result ? <RetainerResults result={result} /> : <ScanResults result={result} />}
    </>
  );
}
export default function History() {
  const [kind, setKind] = useState("");
  const [profile, setProfile] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [search, setSearch] = useState("");
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const pageSize = 50;
  const [list, setList] = useState<Schema<"ArchiveSummary">[]>();
  const [id, setId] = useState<number>();
  const [entry, setEntry] = useState<Entry>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const query = new URLSearchParams({limit: String(pageSize+1), offset: String(offset)});
    if (kind) query.set("kind", kind);
    if (profile) query.set("profile", profile);
    if (search) query.set("search", search);
    if (since) query.set("since", String(new Date(since + "T00:00:00").getTime() / 1000));
    if (until) {
      const end = new Date(until + "T00:00:00");
      end.setDate(end.getDate() + 1);
      query.set("until", String(end.getTime() / 1000));
    }
    setError("");
    api
      .history(query)
      .then((rows) => {
        if (active) setList(rows);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [kind, profile, since, until, search, revision, offset]);
  useEffect(() => {
    let active = true;
    setEntry(undefined);
    if (id !== undefined)
      api
        .entry(id)
        .then((e) => {
          if (active) setEntry(e);
        })
        .catch((e) => {
          if (active) setError(errorMessage(e));
        });
    return () => {
      active = false;
    };
  }, [id]);
  return (
    <>
      <PageTitle title="History" description="Inspect past scans and reuse their settings." />
      <Section title="Archived scans">
        <div className="fields panel-body" onChange={()=>setOffset(0)}>
          <Field label="Kind">
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">All kinds</option>
              {["scan", "snipe", "retainer"].map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </Field>
          <Field label="Profile">
            <input value={profile} onChange={(e) => setProfile(e.target.value)} />
          </Field>
          <Field label="From">
            <input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
          </Field>
          <Field label="Through">
            <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </Field>
          <Field label="Item name">
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          </Field>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {list?.length ? (
          <Table label="Archived scans">
            <thead>
              <tr>
                {["Date", "Kind", "Profile", "Rows", "Duration"].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {list.slice(0,pageSize).map((s) => (
                <tr key={s.id}>
                  <td>
                    <button onClick={() => setId(s.id)}>{time(s.started_ts)}</button>
                  </td>
                  <td>{s.kind}</td>
                  <td>{s.profile ?? "—"}</td>
                  <td>{s.row_count}</td>
                  <td>{number(s.duration_s)} s</td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <Notice>{list ? "No matching scans." : "Loading scans…"}</Notice>
        )}
        {(offset>0 || (list && list.length>pageSize)) && <div className="form-actions">
          <button disabled={offset===0} onClick={()=>setOffset(v=>v-pageSize)}>Previous</button>
          <button disabled={!list || list.length<=pageSize} onClick={()=>setOffset(v=>v+pageSize)}>Next</button>
        </div>}
      </Section>
      {entry && (
        <EntryView
          key={entry.id}
          entry={entry}
          refresh={() => setRevision((v) => v + 1)}
          close={() => setId(undefined)}
        />
      )}
    </>
  );
}
