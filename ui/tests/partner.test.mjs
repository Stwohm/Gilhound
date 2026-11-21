import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
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
      assert.ok(html.includes("Partner " + reason));
      assert.ok(html.includes("You list this"));
      assert.ok(visiblePartnerRow(row,false));
      assert.equal(visiblePartnerRow(row,true),false);
    }
    assert.ok(visiblePartnerRow({you_list:true},true));
  } finally {
    await vite.close();
  }
});
