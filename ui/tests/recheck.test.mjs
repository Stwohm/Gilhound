import test from "node:test";
import assert from "node:assert/strict";
import { reviewQuotes } from "../src/recheck.ts";

const quote = {item:{id:1000,hq:true,name:"Item"}, buy_world:"Away", buy_ppu:10,
  buy_qty_at_price:2, plannedStacks:[2], data_age_h:0.5, home_lowest:30, home_median_7d:40};
const fresh = {price:12,qty:3,age_h:2,stacks:[3],home_lowest:25,home_median:35};

test("recheck precedes confirmation and reuses the snapshot for the same item", async () => {
  const events = [];
  const fetchQuote = async q => {
    events.push("fetch");
    assert.deepEqual(q,{item_id:1000,hq:true,buy_world:"Away",home_world:"Home",history_entries:80});
    return fresh;
  };
  const confirmed = await reviewQuotes([quote,quote],"Home",fetchQuote, message => {
    events.push("confirm");
    for (const diff of ["10 → 12","2 → 3","30 → 25","40 → 35","stale","Stack sizes changed"]) {
      assert.ok(message.includes(diff),diff);
    }
    return false;
  },assert.fail);
  assert.equal(confirmed,false);
  assert.deepEqual(events,["fetch","confirm"]);
});

test("missing listings and transport errors cannot authorize travel",async () => {
  let message;
  const missing = await reviewQuotes([quote],"Home",async()=>({...fresh,price:null,qty:0}),
    assert.fail,text=>{message=text;});
  assert.equal(missing,false);
  assert.match(message,/disappeared/);
  await assert.rejects(reviewQuotes([quote],"Home",async()=>{throw Error("offline");},
    assert.fail,assert.fail),/offline/);
  assert.equal(await reviewQuotes([quote],"Home",async()=>fresh,()=>true,assert.fail),true);
});
