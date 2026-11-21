import { mockParams } from "./mockProfiles";
import type { Api, Library } from "./client";
import type { components } from "./types";

type ScanParams = components["schemas"]["ScanParams"];
type ScanRow = components["schemas"]["ScanRow"];
type Settings = components["schemas"]["UserSettings-Output"];
type SnipeHit = components["schemas"]["SnipeHit"];

export const demoRetainer = "Demo Retainer 01";
const now = () => Math.floor(Date.now() / 1000);
let lastUpdateCheck = now();
const pause = (ms = 150) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

const costs = new Map<string, number>();

let settings: Settings = {
  partner_cache_min: 45, partner_regular_days: 5, partner_window_days: 14,
  suppress_partner_discord: true, share_sender: "",
  min_net_multiple: 1,
  history_days: 90,
  snipe_rule: { max_home_median_ratio: 0.5, max_last_sale_age_h: 24, max_top_buyer_share: 0.5,
    min_distinct_buyers_7d: 3, min_profit: 5000 },
  home_world: "Cerberus",
  buy_region: "Europe",
  auto_snipe_enabled: true,
  discord_coin_emoji: "🪙",
  scan_interval_min: 5,
  discord_notifier: false,
  dedup_cooldown_h: 6,
  freshness_tiers: [
    { name: "fresh", max_age_h: 1, send_discord: true },
    { name: "stale", max_age_h: 6, send_discord: false },
  ],
  travel_cost: { Chaos: 0, Light: 1200 },
};

const inventory = [
  { name: "Moonweave Spool", hq: true, world: "Raiden", dc: "Light", price: 18000, qty: 16, median: 46500, velocity: 4.1, buyers: 16, age: 0.2, sale: 0.6 },
  { name: "Emberglass Ingot", hq: false, world: "Louisoix", dc: "Chaos", price: 11900, qty: 42, median: 29500, velocity: 12.4, buyers: 28, age: 0.63, sale: 0.25 },
  { name: "Starfall Varnish", hq: true, world: "Odin", dc: "Light", price: 28000, qty: 5, median: 72000, velocity: 1.8, buyers: 8, age: 3.4, sale: 4.2 },
  { name: "Ivory Circuit", hq: false, world: "Spriggan", dc: "Chaos", price: 8300, qty: 54, median: 16200, velocity: 15.6, buyers: 37, age: 1.8, sale: 0.1 },
  { name: "Aetherbloom Oil", hq: true, world: "Raiden", dc: "Light", price: 14500, qty: 12, median: 31400, velocity: 3.2, buyers: 10, age: 7.4, sale: 8.1 },
  { name: "Copperleaf Resin", hq: false, world: "Moogle", dc: "Chaos", price: 4700, qty: 87, median: 11500, velocity: 21.6, buyers: 43, age: 0.45, sale: 0.13 },
  { name: "Duskmere Silk", hq: true, world: "Odin", dc: "Light", price: 59000, qty: 4, median: 93000, velocity: 2.2, buyers: 11, age: 4.5, sale: 6.4 },
];

type Catalog = components["schemas"]["ProfileList"];
const catalogs = {} as Record<Library, Catalog>;
for (const library of ["scan", "snipes"] as const) {
  const defaults = Object.fromEntries(Object.entries(mockParams)
    .filter(([name]) => library === "scan" || name === "Snipe")
    .map(([name, params]) => [name, { params: { ...params, travel_cost: settings.travel_cost,
      snipe_rule: name === "Snipe" ? settings.snipe_rule : null,
      freshness_tiers: name === "Snipe" ? settings.freshness_tiers : null } }]));
  catalogs[library] = { defaults, descriptions: {}, profiles: structuredClone(defaults), active: library === "scan" ? "Flip" : "Snipe" };
}
function mockCatalog(library: Library): Catalog {
  const catalog = structuredClone(catalogs[library]);
  for (const [name, { params: p }] of Object.entries(catalog.profiles)) {
    const limits = [
      `at least ${p.min_history_sales} sales in 7 days`,
      p.min_distinct_buyers && `at least ${p.min_distinct_buyers} buyers`,
      p.min_days_with_sales && `sales on at least ${p.min_days_with_sales} days`,
      p.min_units_day && `at least ${p.min_units_day} units/day`,
    ].filter(Boolean);
    const lines = [`Finds items in ${p.buy_region} to sell on ${p.home_world}.`,
      `Requires ${limits.join(", ")}.`,
      `Requires ${p.min_profit ? `at least ${p.min_profit} gil` : "positive"} profit per unit after taxes, capped at ${p.sellable_days} days of sales.`];
    const r = p.snipe_rule;
    if (r) {
      lines.push(`Alerts at up to ${r.max_home_median_ratio * 100}% of the home median, with the last sale under ${r.max_last_sale_age_h} h.`);
      if (r.min_profit) lines.push(`Requires at least ${r.min_profit} gil profit.`);
      if (r.min_distinct_buyers_7d) lines.push(`Requires at least ${r.min_distinct_buyers_7d} buyers.`);
      if (r.max_top_buyer_share < 1) lines.push(`Top buyer share up to ${r.max_top_buyer_share * 100}%.`);
    }
    for (const t of p.freshness_tiers ?? []) lines.push(`${t.name} up to ${t.max_age_h} h: ${t.send_discord ? "app and Discord when enabled" : "app only"}.`);
    catalog.descriptions[name] = lines;
  }
  return catalog;
}
const hidden = new Map<string, components["schemas"]["ItemRef"]>();

function marketRows(params: Pick<ScanParams, "buyer_tax" | "seller_tax" | "sellable_days" | "travel_cost">): ScanRow[] {
  return inventory.map((entry, index): ScanRow => {
    const sellable = Math.min(entry.qty, Math.floor(entry.velocity * (params.sellable_days ?? 3)));
    const profit = entry.median * (1 - params.seller_tax) -
      entry.price * (1 + (params.buyer_tax ?? 0.05)) - (params.travel_cost?.[entry.dc] ?? 0) / Math.max(1,sellable);
    return {
      you_list: index === 1, partner: index === 0 ? "regular" : null,
      partner_detail: index === 0 ? "Partner regular: seen 5 of 14 days" : null,
      item: { id: 91001 + index, name: entry.name, hq: entry.hq,
        universalis_url: `https://universalis.app/market/${91001 + index}?server=${encodeURIComponent(settings.home_world)}`,
        saddlebag_url: `https://saddlebagexchange.com/queries/item-data/${91001 + index}` },
      buy_world: entry.world, buy_dc: entry.dc, buy_ppu: entry.price, buy_qty_at_price: entry.qty,
      home_median_7d: entry.median, home_lowest: index % 4 === 3 ? null : entry.median + 2400,
      median_profit_per_unit: profit, buy_stacks: [entry.qty],
      ask_exit_ppu: index % 4 === 3 ? null : entry.median,
      ask_profit_per_unit: index % 4 === 3 ? null : profit,
      ask_profit_capped: index % 4 === 3 ? null : profit * sellable,
      home_lowest_units: index % 4 === 3 ? 0 : 1,
      sellable_qty: sellable, profit_per_unit: profit, total_profit_capped: sellable * profit,
      est_wait_days: sellable / entry.velocity, days_to_sell: sellable / entry.velocity,
      roi_pct: 100 * profit / (entry.price * (1 + (params.buyer_tax ?? 0.05))),
      capital_needed: sellable * entry.price * (1 + (params.buyer_tax ?? 0.05)),
      profit_per_day: profit * Math.min(entry.velocity,sellable),
      rival_stock: index * 2, distinct_sellers: index + 1, home_listing_count: index + 2,
      units_sold_7d: Math.round(entry.velocity*7), sales_7d: entry.buyers,
      daily_units_14d: Array.from({length:14},(_,i)=>Math.round(entry.velocity*(0.5+(i+index)%5/4))),
      trend_pct: 10-index*4,
      volume_proof: {
        units_per_day: entry.velocity, distinct_buyers_7d: entry.buyers, last_sale_age_h: entry.sale,
        top_buyer_share: 0.23, days_with_sales: Math.min(7, entry.buyers),
      },
      data_age_h: entry.age,
    };
  });
}

function scanRows(params: ScanParams): ScanRow[] {
  return marketRows(params).filter((row) => (
    row.buy_world !== params.home_world &&
    ["europe", row.buy_dc.toLowerCase()].includes(params.buy_region.trim().toLowerCase()) &&
    row.buy_ppu >= (params.min_ppu ?? 0) && row.profit_per_unit >= (params.min_profit ?? 0) &&
    (params.quality === "both" || row.item.hq === (params.quality === "hq")) &&
    row.volume_proof.distinct_buyers_7d >= params.min_distinct_buyers &&
    row.volume_proof.days_with_sales >= params.min_days_with_sales &&
    row.volume_proof.units_per_day >= params.min_units_day &&
    row.volume_proof.top_buyer_share <= params.max_top_buyer_share &&
    (params.max_sale_age_h == null || (row.volume_proof.last_sale_age_h ?? Infinity) < params.max_sale_age_h) &&
    (params.max_data_age_h == null || row.data_age_h < params.max_data_age_h)
  ));
}

function snipeHits(): SnipeHit[] {
  const ts = now();
  const active = catalogs.snipes.profiles[catalogs.snipes.active];
  const rule = active.params.snipe_rule!;
  return scanRows(active.params).filter(row => row.buy_ppu <= row.home_median_7d * rule.max_home_median_ratio &&
    row.profit_per_unit >= rule.min_profit && row.volume_proof.distinct_buyers_7d >= rule.min_distinct_buyers_7d &&
    row.volume_proof.top_buyer_share <= rule.max_top_buyer_share &&
    (row.volume_proof.last_sale_age_h ?? Infinity) < rule.max_last_sale_age_h).flatMap((row, index) => {
    const tier = active.params.freshness_tiers?.find((entry) => row.data_age_h < entry.max_age_h);
    return tier ? [{
      ...row, tier, listing_key: `${row.item.id}-${row.buy_world}`, first_seen_ts: ts - 180 - index * 150,
      notified_ts: settings.discord_notifier && tier.send_discord ? ts - 120 - index * 150 : null,
    }] : [];
  });
}

let hits = snipeHits();
let lastRun = now();
let nextRun = lastRun + 300;

type Entry = components["schemas"]["ArchiveEntry"];
const archive = new Map<number,Entry>();
const retainers: Record<string,components["schemas"]["RetainerScanParams"]> = Object.create(null);
let nextId=1;
function record(result: Entry["result"], profile?:string) {
  const id=nextId++;
  const retainer="items_scanned" in result;
  const started_ts=retainer?now()-1:result.started_ts;
  archive.set(id,{id,kind:retainer?"retainer":result.params.preset==="snipe"?"snipe":"scan",profile:profile??null,
    started_ts,duration_s:retainer?result.duration_s:result.finished_ts-started_ts,row_count:result.rows.length,
    result,description:[]});
}
export const mockApi: Api = {
  async bridge() { return { status: "not_running", mb_search_name: false }; },
  async go() { return { status: "unavailable", reason: "Restocky not running" }; },
  async history(q){return [...archive.values()].reverse().filter(e=>
    (!q.has("kind") || e.kind===q.get("kind")) && (!q.has("profile") || e.profile===q.get("profile"))
    && (!q.has("since") || e.started_ts>=Number(q.get("since"))) && (!q.has("until") || e.started_ts<Number(q.get("until")))
    && (!q.has("search") || e.result.rows.some(r=>r.item.name.toLowerCase().includes(q.get("search")!.toLowerCase())))).slice(Number(q.get("offset")),Number(q.get("offset"))+Number(q.get("limit")??101));},
  async entry(id){const e=archive.get(id);if(!e)throw Error("Entry not found.");return e;},
  async deleteEntry(id){archive.delete(id);},
  async retainerProfiles(){return {...retainers};},
  async saveRetainerProfile(name,params){if(retainers[name])throw Error("Choose a unique name.");retainers[name]=params;},
  async getProfiles(library) { return mockCatalog(library); },
  async changeProfile(library, { action, name, new_name, profile }) {
    const state = catalogs[library];
    const target = action === "rename" ? new_name! : name;
    if (["rename", "create"].includes(action) && Object.keys(state.profiles).some(n => n.toLowerCase() === target.toLowerCase()))
      throw new Error("Choose a unique profile name.");
    if (action === "rename") { state.profiles[target] = state.profiles[name]; delete state.profiles[name]; }
    if (action === "delete") delete state.profiles[name];
    if (action === "save" || action === "create") state.profiles[name] = structuredClone(profile!);
    if (action === "reset") state.profiles[name] = structuredClone(Object.values(state.defaults)
      .find(p => p.params.preset === state.profiles[name].params.preset)!);
    if (library === "snipes") state.active = action === "delete" ? "Snipe" : target;
    return mockCatalog(library);
  },
  async getHidden() { return [...hidden.values()]; },
  async setPersonal(change) {
    const key = change.item_id + "-" + change.hq;
    if (!change.personal) hidden.delete(key);
    else {
      const item = marketRows(mockParams.Flip).find(r => r.item.id === change.item_id && r.item.hq === change.hq)?.item;
      if (item) hidden.set(key, item);
    }
  },
  async scan(params,profile) {
    const started_ts = now();
    await pause(800);
    const result = { params, rows: scanRows(params), started_ts, finished_ts: now() };
    record(result,profile); return result;
  },
  async retainerScan(params,profile) {
    await pause(1000);
    const region = params.buy_region ?? settings.buy_region;
    const pricedListings = marketRows({
      buyer_tax: 0.05, seller_tax: 0.05, sellable_days: 3, travel_cost: settings.travel_cost,
    });
    const regionKey = region.trim().toLowerCase();
    const regional = pricedListings.filter((row) => regionKey === "europe" || regionKey === row.buy_dc.toLowerCase());
    const result = {
      params, items_scanned: 16900, duration_s: 1,
      rows: params.retainer_name.trim().toLowerCase() === demoRetainer.toLowerCase() && (params.world ?? settings.home_world) === "Cerberus"
        ? pricedListings.slice(0, 4).map((row, index) => {
          const cheapest = index === 3 ? undefined : regional.find((candidate) => candidate.item.id === row.item.id);
          return {
            partner: row.partner, partner_detail: row.partner_detail, you_list: row.you_list,
            item: row.item, their_ppu: row.home_median_7d, qty: index + 2, data_age_h: 0.22 + index * 0.8,
            cheapest_world: cheapest?.buy_world ?? null, cheapest_dc: cheapest?.buy_dc ?? null,
            cheapest_ppu: cheapest?.buy_ppu ?? null, cheapest_qty: cheapest?.buy_qty_at_price ?? null,
            gap_pct: cheapest ? (1 - cheapest.buy_ppu / row.home_median_7d) * 100 : null,
            profit_per_unit: cheapest?.profit_per_unit ?? null,
          };
        }) : [],
    };
    record(result,profile); return result;
  },
  async getSnipeStatus() {
    if (settings.auto_snipe_enabled && now() >= nextRun) {
      hits = snipeHits();
      lastRun = now();
      record({params: catalogs.snipes.profiles[catalogs.snipes.active].params, rows: hits,
        started_ts: lastRun, finished_ts: now()}, catalogs.snipes.active);
      nextRun = lastRun + (settings.scan_interval_min ?? 5) * 60;
    }
    return { running: false, next_run_ts: settings.auto_snipe_enabled ? nextRun : null,
      last_run_ts: lastRun, fresh: hits.filter(h => h.tier.name === "fresh").length,
      stale: hits.filter(h => h.tier.name === "stale").length, notified: hits.filter(h => h.notified_ts).length };
  },
  async getSnipes() {
    await pause();
    return hits;
  },
  async runSnipes() {
    await pause(750);
    lastRun = now();
    const previous = new Map(hits.map((hit) => [hit.listing_key, hit]));
    hits = snipeHits().map((hit) => ({
      ...hit, first_seen_ts: previous.get(hit.listing_key)?.first_seen_ts ?? hit.first_seen_ts,
      notified_ts: previous.get(hit.listing_key)?.notified_ts ?? hit.notified_ts,
    }));
    record({params:catalogs.snipes.profiles[catalogs.snipes.active].params,rows:hits,started_ts:lastRun,finished_ts:now()},catalogs.snipes.active);
    return hits;
  },
  async getSources() {
    await pause();
    return [
      { name: "cashflow", found: true, recognised: true, last_update_ts: now() - 720 },
      { name: "allagan_market", found: true, recognised: true, last_update_ts: now() - 3600 * 24 * 3 },
      { name: "allagan_tools", found: true, recognised: false, warning: "Inventory file format not recognised." },
      { name: "restocky", found: false, recognised: false, warning: "Source files were not found." },
    ];
  },
  async setCost(cost) { costs.set(`${cost.item_id}-${cost.hq}`, cost.unit_cost); },
  async getSales() {
    return marketRows(mockParams.Flip).slice(0,3).map((r,i) => {
      const gross = r.home_median_7d * 2, net = Math.floor(gross * 0.95);
      const basis = i === 2 ? null : Math.floor(2 * r.buy_ppu * 1.05);
      return {item:r.item, qty:2, ts:now() - (i+1)*86400, gross, net, fifo_cost:basis,
        buy_ppu:basis == null ? null : basis/2, gross_ppu:gross/2, net_ppu:net/2,
        profit:basis == null ? null : net-basis, known_profit:basis == null ? 0 : net-basis, roi_pct:basis ? 100*(net-basis)/basis : null,
        unknown_qty:basis == null ? 2 : 0, sources:["restocky"], listing_days:i+0.5,
        purchase_days:i+1, retainer:demoRetainer, reprices:i, own_undercuts:i};
    });
  },
  async recheck(q) {
    const row = marketRows(mockParams.Flip).find(r => r.item.id === q.item_id && r.item.hq === q.hq && r.buy_world === q.buy_world);
    return { price: row?.buy_ppu ?? null, qty: row?.buy_qty_at_price ?? 0, age_h: row?.data_age_h ?? null,
      stacks: row?.buy_stacks ?? [], home_lowest: row?.home_lowest ?? null, home_median: row?.home_median_7d ?? null };
  },
  async getPositions() {
    await pause();
    const rows = marketRows({
      buyer_tax: 0.05, seller_tax: 0.05, sellable_days: 3,
    });
    return rows.slice(0, 5).map((row, index) => ({
      item: row.item, qty_open: index === 4 ? 0 : row.sellable_qty + 3,
      avg_cost: index === 3 ? costs.get(`${row.item.id}-${row.item.hq}`) ?? null : row.buy_ppu,
      cost_known: index !== 3 || costs.has(`${row.item.id}-${row.item.hq}`),
      known_capital: index === 4 ? 0 : (row.sellable_qty + 3) * (index === 3 ? costs.get(`${row.item.id}-${row.item.hq}`) ?? 0 : row.buy_ppu),
      listing_count: index === 4 ? 0 : 1,
      listed_qty: index === 4 ? 0 : row.sellable_qty, realised_profit: index === 3 ? 0 : 42500 + index * 16700,
    })).filter(r => !hidden.has(r.item.id + "-" + r.item.hq));
  },
  async getPnl() {
    await pause();
    const rows = await mockApi.getPositions();
    const known = rows.filter(r => r.cost_known);
    const cost = known.reduce((sum, r) => sum + (r.avg_cost ?? 0), 0);
    const profit = known.reduce((sum, r) => sum + r.realised_profit, 0);
    const unknown = rows.length - known.length;
    return { realised_profit: profit, known_cost_of_goods_sold: cost,
      sales_proceeds: profit + cost + unknown * 1000, unknown_cost_qty_sold: unknown };
  },
  async getSettings() {
    await pause();
    return structuredClone(settings);
  },
  async saveSettings(input) {
    await pause();
    const { webhook_url, ...fields } = input;
    if (!settings.auto_snipe_enabled && fields.auto_snipe_enabled) nextRun = now();
    else if (settings.scan_interval_min !== fields.scan_interval_min) nextRun = now() + (fields.scan_interval_min ?? 5) * 60;
    settings = {
      ...fields,
      webhook_url: webhook_url === undefined ? settings.webhook_url : webhook_url ? "**********" : null,
    };
    return structuredClone(settings);
  },
  async getInfo() {
    await pause();
    return { version: "0.1.3", updates_supported: true, latest_version: "0.1.4",
      update_available: true, update_checked_at: lastUpdateCheck };
  },
  async checkUpdate() {
    lastUpdateCheck = now();
    return mockApi.getInfo();
  },
  async applyUpdate(progress) {
    for (let done = 0; done <= 100; done += 25) {
      progress(done, 100);
      await pause();
    }
  },
  async exportDiagnostics() {
    await pause();
    // ZIP with an empty central directory. The demo has no diagnostic logs.
    return new Blob([new Uint8Array([80, 75, 5, 6, ...Array<number>(18).fill(0)])], { type: "application/zip" });
  },
  async testDiscord() {
    await pause(450);
  },
};
