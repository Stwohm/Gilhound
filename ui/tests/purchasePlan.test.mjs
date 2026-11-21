import test from "node:test";
import assert from "node:assert/strict";
import {purchasePlan} from "../src/purchasePlan.ts";

test("a route uses indivisible stacks, one visit per world and one sales cap per quality", () => {
  const row = {item: {id: 1,hq:false}, buy_world:"Away", buy_dc:"DC", buy_ppu:51,
    volume_proof:{units_per_day:1}, buy_stacks:[2,4], buy_qty_at_price:6, home_median_7d:150,
    ask_exit_ppu:100};
  const chosen = [{row,i:0},{row:{...row, buy_stacks:[2], buy_qty_at_price:2},i:1}];
  const params = {buyer_tax:0.05,seller_tax:0.05,travel_cost:{DC:10},sellable_days:3,
    large_buy_gil:200,min_net_multiple:1};
  const plan = purchasePlan(chosen,params,new Set(["0/1"]));
  assert.deepEqual(plan.preview.map(p=>[p.qty,p.units]),[[2,2],[2,1]]);
  assert.deepEqual([plan.travel,plan.outlay,plan.net,plan.bandFailed],[10,214,61,true]);
  const twoStops = purchasePlan([chosen[0],{row:{...chosen[1].row,buy_world:"Other"},i:1}],params,new Set());
  assert.equal(twoStops.travel,20);
  const oneStop = purchasePlan(chosen,params,new Set(["0/0","0/1"]));
  assert.deepEqual([oneStop.purchases.length,oneStop.outlay,oneStop.travel,oneStop.bandFailed],[1,107,10,false]);
});
