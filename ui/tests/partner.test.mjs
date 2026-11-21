import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";
import { Window } from "happy-dom";
import { act, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("partner reasons render with a tooltip and can be hidden independently of own listings", async () => {
  const vite = await createServer({root: fileURLToPath(new URL("../", import.meta.url)),
    server:{middlewareMode:true, hmr:false}, appType:"custom"});
  try {
    const {PartnerBadge,visiblePartnerRow} = await vite.ssrLoadModule("/src/Partner.tsx");
    for (const reason of ["now","regular","manual"]) {
      const row = {partner:reason, partner_detail:"observed evidence", you_list:true};
      const html = renderToStaticMarkup(createElement(PartnerBadge,{row}));
      assert.match(html, /title="observed evidence"/);
      assert.ok(html.includes(reason === "now" ? "🤝 observed evidence" : "Partner " + reason));
      assert.ok(html.includes("You list this"));
      assert.ok(visiblePartnerRow(row,false));
      assert.equal(visiblePartnerRow(row,true),false);
    }
    assert.ok(visiblePartnerRow({you_list:true},true));
  } finally {
    await vite.close();
  }
});

async function mount(t, render) {
  const vite = await createServer({root:fileURLToPath(new URL("../", import.meta.url)),
    server:{middlewareMode:true,hmr:false},appType:"custom"});
  const window = new Window();
  const previous = new Map();
  for (const [key,value] of Object.entries({window, document:window.document,
    navigator:window.navigator, IS_REACT_ACT_ENVIRONMENT:true})) {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis,key));
    Object.defineProperty(globalThis,key,{value, configurable:true});
  }
  const {createRoot} = await import("react-dom/client");
  const container = document.body.appendChild(document.createElement("div"));
  const root = createRoot(container);
  t.after(async () => {
    await act(() => root.unmount());
    await vite.close();
    await window.happyDOM.close();
    for (const [key,descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis,key,descriptor);
      else delete globalThis[key];
    }
  });
  const element = await render(vite);
  await act(() => root.render(element));
  return container;
}

test("select all shows five of six visible rows selected after filtering", async t => {
  const container = await mount(t, async vite => {
    const {ScanResults} = await vite.ssrLoadModule("/src/screens/Scan.tsx");
    const {api} = await vite.ssrLoadModule("/src/api/client.ts");
    const {mockParams} = await vite.ssrLoadModule("/src/api/mockProfiles.ts");
    const result = await api.scan(mockParams.Flip);
    return createElement(ScanResults,{result});
  });
  const rows = () => [...container.querySelectorAll("tbody .select-column input")];
  assert.equal(rows().length,7);
  for (const input of rows().slice(0,6)) await act(() => input.click());
  const hide = [...container.querySelectorAll("label")].find(l => l.textContent.includes("Hide partner items"));
  await act(() => hide.querySelector("input").click());
  const header = container.querySelector("thead .select-column input");
  assert.equal(rows().length,6);
  assert.equal(rows().filter(input => input.checked).length,5);
  assert.equal(header.checked,false);
  assert.equal(header.indeterminate,true);
  assert.match(container.textContent,/Copy results \(5\)/);
  await act(() => header.click());
  assert.ok(rows().every(input => input.checked));
  assert.equal(header.indeterminate,false);
});

test("Settings exports, copies and previews the mock snapshot", async t => {
  t.mock.method(globalThis,"fetch",async () => new Response(null,{status:404}));
  const container = await mount(t, async vite => {
    const {PartnerSettings} = await vite.ssrLoadModule("/src/Partner.tsx");
    return createElement(PartnerSettings,{draft:{home_world:"Home",share_sender:"Demo",
      own_avoid:[{id:42,hq:true,note:"Sample"}]},change:()=>{},hasWebhook:false});
  });
  const click = async label => {
    const button = [...container.querySelectorAll("button")].find(b => b.textContent === label);
    await act(() => button.click());
  };
  async function until(check) {
    for (let i=0; i<100 && !check(); i++) await act(() => new Promise(r => setTimeout(r,10)));
    assert.ok(check(), container.querySelector('[role="status"]')?.textContent);
  }
  let file, name, copied;
  t.mock.method(URL,"createObjectURL",blob => { file = blob; return "blob:mock"; });
  t.mock.method(window.HTMLAnchorElement.prototype,"click",function () { name = this.download; });
  t.mock.method(navigator.clipboard,"writeText",async value => { copied = value; });
  await click("Export file");
  await until(() => file);
  assert.equal(name,"items.gilhound-share");
  const exported = await file.text();
  assert.match(exported,/^GH2\.[\w-]+$/);
  const payload = JSON.parse(inflateSync(Buffer.from(exported.slice(4),"base64url")).toString());
  assert.equal(payload.version,2);
  assert.deepEqual(payload.items,[{id:42,hq:true,note:"Sample"}]);
  await click("Copy share code");
  await until(() => copied);
  assert.equal(container.querySelector("textarea").value,copied);
  await click("Preview import");
  await until(() => container.textContent.includes("1 active listings, 1 avoid items, from Demo"));
  assert.match(container.textContent,/42\/HQ/);
});
