import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { Window } from "happy-dom";
import { act, createElement } from "react";
import { createServer } from "vite";
import { mergeColumnOrder, moveColumn } from "../src/columnOrder.ts";

test("merge drops retired and duplicate labels and appends new columns in default order", () => {
  assert.deepEqual(
    mergeColumnOrder(["Item", "Price", "Quantity", "Age"], ["Price", "Retired", "Price", "Item"]),
    ["Price", "Item", "Quantity", "Age"],
  );
});

test("an empty saved order restores the default positions", () => {
  assert.deepEqual(mergeColumnOrder(["Item", "Price"], []), ["Item", "Price"]);
});

test("moving visible columns leaves hidden Scan columns in their slots", () => {
  const order = ["Item", "Sales", "Price", "Age", "Quantity"];
  assert.deepEqual(moveColumn(order, ["Item", "Price", "Quantity"], "Quantity", "Item"),
    ["Quantity", "Sales", "Item", "Age", "Price"]);
  assert.deepEqual(moveColumn(order, ["Item", "Price", "Quantity"], "Item", "Quantity"),
    ["Price", "Sales", "Quantity", "Age", "Item"]);
  assert.equal(moveColumn(order, ["Item", "Price", "Quantity"], "Item", "Age"), order);
});

test("drag and keyboard moves keep headers and cells aligned without sorting", async t => {
  const vite = await createServer({
    root: fileURLToPath(new URL("../", import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: "custom",
  });
  const window = new Window({ url: "http://localhost" });
  const globals = { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true };
  const previous = Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  for (const [key, value] of Object.entries(globals)) {
    Object.defineProperty(globalThis, key, { value, configurable: true });
  }
  const { createRoot } = await import("react-dom/client");
  const root = createRoot(window.document.body);
  t.after(async () => {
    await act(async () => root.unmount());
    await vite.close();
    await window.happyDOM.close();
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  const { ColumnOrderProvider, Table } = await vite.ssrLoadModule("/src/common.tsx");
  const { api } = await vite.ssrLoadModule("/src/api/client.ts");
  let saved = [];
  t.mock.method(api, "getColumnOrder", async () => ({ results: saved }));
  t.mock.method(api, "saveColumnOrder", async (_, order) => { saved = order; return order; });

  let sorts = 0;
  const h = createElement;
  const render = key => root.render(h(ColumnOrderProvider, { key },
    h(Table, { label: "Results", tableKey: "results", defaults: ["Item", "Hidden", "Price"] },
      h("thead", null, h("tr", null,
        h("th", { "data-column": "Item" }, h("button", { onClick: () => sorts++ }, "Item")),
        h("th", null, "Price"))),
      h("tbody", null, h("tr", null, h("td", null, "Thread"), h("td", null, "20"))))));
  await act(async () => render(0));
  const button = label => [...window.document.querySelectorAll("button")]
    .find(element => element.textContent === label || element.getAttribute("aria-label") === label);
  const values = () => [...window.document.querySelectorAll("td")].map(cell => cell.textContent);
  const drag = new window.DataTransfer();
  const dragEvent = name => {
    const event = new window.DragEvent(name, { bubbles: true });
    Object.defineProperty(event, "dataTransfer", { value: drag });
    return event;
  };
  await act(async () => {
    button("Reorder Item").dispatchEvent(dragEvent("dragstart"));
    button("Reorder Price").closest("th").dispatchEvent(dragEvent("drop"));
  });
  assert.deepEqual(values(), ["20", "Thread"]);
  assert.deepEqual(saved, ["Price", "Hidden", "Item"]);
  assert.equal(sorts, 0);
  await act(async () => render(1));
  assert.deepEqual(values(), ["20", "Thread"]);
  await act(async () => button("Reorder Price").click());
  await act(async () => button("Move right").click());
  assert.deepEqual(values(), ["Thread", "20"]);
  await act(async () => button("Item").click());
  assert.equal(sorts, 1);
  await act(async () => button("Restore default order").click());
  assert.deepEqual(saved, []);
});
