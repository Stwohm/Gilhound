import { components } from "../../../openapi.json";
import { Table } from "../common";
import type { Schema } from "../common";

const groups = {
  windows: components.schemas.CandidateWindow.properties,
  freshness_tiers: components.schemas.FreshnessTier.properties,
  snipe_rule: components.schemas.SnipeRule.properties,
};
type Metadata = Record<string, { description: string }>;

function parameters(value: unknown, baseline: unknown, description: string, label: string,
  metadata?: Metadata): { label: string; value: string; description: string; changed: boolean }[] {
  if ((value && typeof value === "object") || (baseline && typeof baseline === "object")) {
    const current = (value ?? {}) as Record<string, unknown>;
    const defaults = (baseline ?? {}) as Record<string, unknown>;
    const keys = new Set([...Object.keys(current), ...Object.keys(defaults)]);
    if (keys.size) return [...keys].flatMap(key => {
      const indexed = Array.isArray(value) || Array.isArray(baseline);
      const suffix = indexed ? String(Number(key) + 1) : key.replaceAll("_", " ");
      return parameters(current[key], defaults[key], metadata?.[key]?.description ?? description,
        label + " · " + suffix, indexed ? metadata : undefined);
    });
  }
  const display = value == null ? (value === undefined ? "Absent" : "Unset")
    : typeof value === "object" ? "None" : String(value);
  return [{ label, value: display, description, changed: JSON.stringify(value) !== JSON.stringify(baseline) }];
}

export default function ProfileSummary({ name, catalog }: { name: string; catalog: Schema<"ProfileList"> }) {
  const params = catalog.profiles[name].params;
  const baseline = Object.values(catalog.defaults).find(p => p.params.preset === params.preset)!.params;
  const rows = (Object.keys(params) as (keyof typeof params)[]).flatMap(key =>
    parameters(params[key], baseline[key], components.schemas.ScanParams.properties[key].description,
      key.replaceAll("_", " "), groups[key as keyof typeof groups]));
  return <section className="profile-summary" aria-label={name + " summary"}>
    <h3>{name}</h3>
    {catalog.descriptions[name]?.map((line, i) => <p key={i}>{line}</p>)}
    <details><summary>All parameters</summary>
      <Table label="Profile parameters"><thead><tr><th>Parameter</th><th>Value</th><th>Description</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.label}><th scope="row">{row.label}</th>
          <td>{row.value} {row.changed && <small className="badge gold">Changed</small>}</td>
          <td>{row.description}</td></tr>)}</tbody>
      </Table>
    </details>
  </section>;
}
