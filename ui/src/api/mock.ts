import type { Api } from "./client";
import type { components } from "./types";

type ScanParams = components["schemas"]["ScanParams"];
type ScanRow = components["schemas"]["ScanRow"];
type Settings = components["schemas"]["UserSettings-Output"];
type SnipeHit = components["schemas"]["SnipeHit"];

export const demoRetainer = "Demo Retainer 01";
const now = () => Math.floor(Date.now() / 1000);
const pause = (ms = 150) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

const costs = new Map<string, number>();

let settings: Settings = {
  snipe_rule: { max_home_median_ratio: 0.5, max_last_sale_age_h: 24, max_top_buyer_share: 0.5,
    min_distinct_buyers_7d: 3, min_profit: 5000 },
  home_world: "Cerberus",
  buy_region: "Europe",
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

function marketRows(params: Pick<ScanParams, "buyer_tax" | "seller_tax" | "sellable_days" | "travel_cost">): ScanRow[] {
  return inventory.map((entry, index): ScanRow => {
    const sellable = Math.min(entry.qty, Math.floor(entry.velocity * (params.sellable_days ?? 3)));
    const profit = entry.median * (1 - params.seller_tax) -
      entry.price * (1 + (params.buyer_tax ?? 0.05)) - (params.travel_cost?.[entry.dc] ?? 0);
    return {
      item: { id: 91001 + index, name: entry.name, hq: entry.hq,
        universalis_url: `https://universalis.app/market/${91001 + index}?server=${encodeURIComponent(settings.home_world)}`,
        saddlebag_url: `https://saddlebagexchange.com/queries/item-data/${91001 + index}` },
      buy_world: entry.world, buy_dc: entry.dc, buy_ppu: entry.price, buy_qty_at_price: entry.qty,
      home_median_7d: entry.median, home_lowest: index % 4 === 3 ? null : entry.median + 2400,
      sellable_qty: sellable, profit_per_unit: profit, total_profit_capped: sellable * profit,
      est_wait_days: sellable / entry.velocity,
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
    (params.preset !== "snipe" || row.buy_ppu <= row.home_median_7d * 0.5) &&
    (params.preset !== "volume" || row.volume_proof.units_per_day >= 10) &&
    (params.preset !== "high_margin" || row.profit_per_unit >= 15000)
  ));
}

function snipeHits(): SnipeHit[] {
  const ts = now();
  return scanRows({
    preset: "snipe", home_world: settings.home_world, buy_region: settings.buy_region,
    buyer_tax: 0.05, seller_tax: 0.05, travel_cost: settings.travel_cost,
    min_ppu: 0, min_profit: 5000, sellable_days: 3,
  }).flatMap((row, index) => {
    const tier = settings.freshness_tiers?.find((entry) => row.data_age_h < entry.max_age_h);
    return tier ? [{
      ...row, tier, listing_key: `${row.item.id}-${row.buy_world}`, first_seen_ts: ts - 180 - index * 150,
      notified_ts: settings.discord_notifier && tier.send_discord ? ts - 120 - index * 150 : null,
    }] : [];
  });
}

let hits = snipeHits();

export const mockApi: Api = {
  async scan(params) {
    const started_ts = now();
    await pause(800);
    return { params, rows: scanRows(params), started_ts, finished_ts: now() };
  },
  async retainerScan(params) {
    await pause(1000);
    const region = params.buy_region ?? settings.buy_region;
    const pricedListings = marketRows({
      buyer_tax: 0.05, seller_tax: 0.05, sellable_days: 3, travel_cost: settings.travel_cost,
    });
    const regionKey = region.trim().toLowerCase();
    const regional = pricedListings.filter((row) => regionKey === "europe" || regionKey === row.buy_dc.toLowerCase());
    return {
      params, items_scanned: 16900, duration_s: 1,
      rows: params.retainer_name.trim().toLowerCase() === demoRetainer.toLowerCase() && (params.world ?? settings.home_world) === "Cerberus"
        ? pricedListings.slice(0, 4).map((row, index) => {
          const cheapest = index === 3 ? undefined : regional.find((candidate) => candidate.item.id === row.item.id);
          return {
            item: row.item, their_ppu: row.home_median_7d, qty: index + 2, data_age_h: 0.22 + index * 0.8,
            cheapest_world: cheapest?.buy_world ?? null, cheapest_dc: cheapest?.buy_dc ?? null,
            cheapest_ppu: cheapest?.buy_ppu ?? null, cheapest_qty: cheapest?.buy_qty_at_price ?? null,
            gap_pct: cheapest ? (1 - cheapest.buy_ppu / row.home_median_7d) * 100 : null,
            profit_per_unit: cheapest?.profit_per_unit ?? null,
          };
        }) : [],
    };
  },
  async getSnipes() {
    await pause();
    return hits;
  },
  async runSnipes() {
    await pause(750);
    const previous = new Map(hits.map((hit) => [hit.listing_key, hit]));
    hits = snipeHits().map((hit) => ({
      ...hit, first_seen_ts: previous.get(hit.listing_key)?.first_seen_ts ?? hit.first_seen_ts,
      notified_ts: previous.get(hit.listing_key)?.notified_ts ?? hit.notified_ts,
    }));
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
      listed_qty: index === 4 ? 0 : row.sellable_qty, realised_profit: index === 3 ? 0 : 42500 + index * 16700,
    }));
  },
  async getPnl() {
    await pause();
    return { realised_profit: 303600, sales_proceeds: 987200, known_cost_of_goods_sold: 626700, unknown_cost_qty_sold: 5 };
  },
  async getSettings() {
    await pause();
    return structuredClone(settings);
  },
  async saveSettings(input) {
    await pause();
    const { webhook_url, ...fields } = input;
    settings = {
      ...fields,
      webhook_url: webhook_url === undefined ? settings.webhook_url : webhook_url ? "**********" : null,
    };
    return structuredClone(settings);
  },
  async getInfo() {
    await pause();
    return { version: "0.1.0", latest_version: "0.1.1", update_available: true };
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
