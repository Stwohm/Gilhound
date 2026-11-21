import type { components } from "./api/types";

type Schemas = components["schemas"];

export function purchasePlan(chosen: {row: Schemas["ScanRow"]; i:number}[],
  params: Schemas["ScanParams"], skippedStacks: ReadonlySet<string>) {
  const remainingDemand = new Map<string, number>();
  const preview = chosen.map(({row, i}) => {
    const stacks = row.buy_stacks?.filter((_, n) => !skippedStacks.has(i + "/" + n));
    const qty = stacks?.reduce((total, n) => total + n, 0) ?? row.buy_qty_at_price;
    const key = row.item.id + "/" + row.item.hq;
    const demand = remainingDemand.get(key) ?? Math.floor(row.volume_proof.units_per_day * params.sellable_days);
    const units = Math.min(demand, qty);
    remainingDemand.set(key, demand - units);
    const tax = params.buyer_tax;
    const cost = stacks?.reduce((total,n) => {
      const subtotal = n * row.buy_ppu;
      return total + subtotal + Math.floor(subtotal * tax + 1e-9);
    }, 0) ?? qty * row.buy_ppu * (1 + tax);
    return {row, i, units, qty, cost, stacks};
  });
  const purchases = preview.filter(line => line.qty > 0);
  const travel = [...new Map(purchases.map(({row}) => [row.buy_world, params.travel_cost?.[row.buy_dc] ?? 0])).values()]
    .reduce((sum, cost) => sum + cost, 0);
  const outlay = purchases.reduce((sum, line) => sum + line.cost, 0);
  const proceeds = purchases.reduce((sum, {row, units}) => sum + units * (row.ask_exit_ppu ?? row.home_median_7d) * (1-params.seller_tax), 0);
  const net = proceeds - travel - outlay;
  const bandFailed = params.large_buy_gil != null && outlay >= params.large_buy_gil &&
    outlay > 0 && net / outlay < params.min_net_multiple;
  return {preview, purchases, travel, outlay, net, bandFailed};
}
