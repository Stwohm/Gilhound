import type { components } from "./api/types";

export type Quote = {
  item: components["schemas"]["ItemRef"]; buy_world: string; buy_ppu: number; buy_qty_at_price: number;
  data_age_h?: number; home_lowest?: number | null; home_median_7d?: number;
  plannedStacks?: number[]; home?: string; maxAge?: number; historyEntries?: number;
};

type Snapshot = components["schemas"]["QuoteSnapshot"];
type FetchQuote = (query: components["schemas"]["QuoteRequest"]) => Promise<Snapshot>;

export async function reviewQuotes(
  quotes: Quote[], home: string, fetchQuote: FetchQuote,
  confirm: (message: string) => boolean, alert: (message: string) => void,
): Promise<boolean> {
  const lines: string[] = [];
  let missing = false;
  const snapshots = new Map<string, components["schemas"]["QuoteSnapshot"]>();
  for (const q of quotes) {
    const query = {item_id: q.item.id, hq: q.item.hq, buy_world: q.buy_world,
      home_world: q.home ?? home, history_entries: q.historyEntries ?? 80};
    const key = JSON.stringify(query);
    const fresh = snapshots.get(key) ?? await fetchQuote(query);
    snapshots.set(key, fresh);
    const fmt = (n: number | null | undefined) => n == null ? "unknown" : n.toLocaleString(undefined, {maximumFractionDigits: 1});
    lines.push(q.item.name + (q.item.hq ? " HQ · " : " NQ · ") + q.buy_world);
    if (fresh.price == null) { lines.push("Listing disappeared."); missing = true; }
    else {
      if (q.plannedStacks) {
        const remaining = [...fresh.stacks];
        const changed = q.plannedStacks.some(n => {
          const index = remaining.indexOf(n);
          if (index < 0) return true;
          remaining.splice(index,1);
          return false;
        });
        if (changed) lines.push("Stack sizes changed · now " + fresh.stacks.join(" + "));
      }
      if (fresh.price > q.buy_ppu) lines.push("Previous price disappeared.");
      lines.push("Price " + fmt(q.buy_ppu) + " → " + fmt(fresh.price) + " gil · qty " + q.buy_qty_at_price + " → " + fresh.qty,
        "Last seen (scan → recheck) " + fmt(q.data_age_h) + " → " + fmt(fresh.age_h) + " h" +
        (fresh.age_h == null || fresh.age_h >= (q.maxAge ?? 1) ? " · still stale / unverified" : ""));
    }
    lines.push("Home ask " + fmt(q.home_lowest) + " → " + fmt(fresh.home_lowest) +
      " · median " + fmt(q.home_median_7d) + " → " + fmt(fresh.home_median), "");
  }
  if (missing) { alert(lines.join("\n")); return false; }
  return confirm(lines.join("\n") + "Continue with travel?");
}
