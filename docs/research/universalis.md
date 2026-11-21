# Universalis API and retainer lookup

Checked 2026-11-21 UTC against the documentation and API source on GitHub
and the live REST service.

## WebSocket

Connect to `wss://universalis.app/api/ws`. Send and receive binary BSON frames.
Send a document with `event` set to `subscribe` or `unsubscribe` and `channel`
set to the desired subscription. The docs list `listings/add`,
`listings/remove`, `sales/add` and `sales/remove`. The current source also
defines `item/update` with an update dispatcher. F0-B verified the source
and contract and did not measure live event delivery.
[WebSocket docs source](https://github.com/Universalis-FFXIV/universalis-docs/blob/main/components/WebSocketDocumentation/WebSocketDocumentation.tsx),
[WebSocket routes](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Controllers/V2/WebSocketController.cs),
[ItemUpdate message](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Realtime/Messages/ItemUpdate.cs),
[update dispatcher](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Realtime/Dispatchers/ItemUpdateDispatcher.cs).

The server supports comma-separated equality filters appended to the channel,
such as `listings/add{world=80,item=5}`. Both filters must match. Subscribe
separately to receive events for multiple worlds or items. The server exposes
`world` and `item` as matchable fields for listing and sales additions.
Uploads drive the events, so an event-triggered refresh can cut polling
without changing how recently an item was seen in game. Use REST to get
the initial snapshot and to recover state after a disconnect.
[Filter parser](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Realtime/Messages/EventCondition.cs),
[listing event](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Realtime/Messages/ListingsAdd.cs),
[sales event](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Realtime/Messages/SalesAdd.cs).

## REST limits and epochs

The documented API limit is **25 requests/second**, with a **50 requests/second
burst** and at most **8 simultaneous connections per IP**. The public site
has a separate **15 requests/second** limit and **30 requests/second burst**.
[Rate limits in documentation source](https://github.com/Universalis-FFXIV/universalis-docs/blob/main/components/RestWelcome/RestWelcome.tsx).

| REST v2 field | Unix epoch unit |
| --- | --- |
| History `entries[].timestamp`, current `recentHistory[].timestamp` | Seconds |
| Current `listings[].lastReviewTime` | Seconds in current source and live sample |
| Current or history `lastUploadTime` | Milliseconds |
| Current `worldUploadTimes` map values | Milliseconds |
| Item recency `lastUploadTime` | Milliseconds |
| Aggregated `recentPurchase` entry and `worldUploadTimes[]` `timestamp` | Milliseconds in the live sample |

The live check returned 10-digit listing review and sale timestamps and a
13-digit upload timestamp. This differs from the F0 plan's assumed review-time
unit. Convert milliseconds at the boundary, with a magnitude check if accepting
older data containing millisecond review timestamps.
[Sale view](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Views/V1/SaleView.cs),
[listing view](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Views/V1/ListingView.cs),
[current view](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Views/V1/CurrentlyShownView.cs),
[history view](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Views/V1/HistoryView.cs),
[item recency view](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Views/V1/Extra/Stats/WorldItemRecencyView.cs),
[live aggregated sample](https://universalis.app/api/v2/aggregated/Cerberus/5).

## Retainer lookup on one world

The public Universalis REST and Saddlebag Exchange API contracts expose no
complete current-listings lookup by retainer name. Saddlebag's undercut alerts
accept retainer names and search a limited slice of the market. Universalis
listing responses expose `retainerName` on item-based queries.
[Universalis OpenAPI](https://universalis.app/swagger/v2/swagger.json),
[Saddlebag OpenAPI](https://docs.saddlebagexchange.com/openapi.json),
[Saddlebag alert coverage](https://saddlebagexchange.com/blog/ffxiv/undercut-alerts-alpha-version).

Fetch and cache `GET /api/v2/marketable`. Batch the IDs into comma-separated
groups of up to **100** for `GET /api/v2/{world}/{itemIds}`. Use `entries=0`
and a `fields` projection for each item's listing retainer, price, quantity,
HQ flag and review time. Omit `listings` to keep all listings returned by the
service. Match retainer names client-side and track unresolved items.
The result covers the listings uploaded to Universalis.
[Marketable controller](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Controllers/V1/MarketableController.cs),
[bulk controller](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Controllers/V2/CurrentlyShownController.cs).

The live item list returned **16,845 IDs**, so one world requires **169 bulk
requests** plus one request when refreshing the item list. One live Cerberus
request for the first 100 sorted IDs returned HTTP 200 and all 100 item
objects in **0.106 seconds**, with **101,518 bytes** of decoded JSON. It used
`entries=0` and
`fields=items.listings.retainerName,items.listings.worldID,items.listings.pricePerUnit,items.listings.quantity,items.listings.hq,items.listings.lastReviewTime,unresolvedItems`.
Applying that single-request latency gives about **18 seconds serially** for
169 batches. Popular item groups, payload size, throttling and retries can
change that estimate.
