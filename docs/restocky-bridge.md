# Restocky bridge (Gilhound → game commands)

Gilhound's **Go** button asks the Restocky Dalamud plugin to run a Lifestream
travel command inside the game. No keystrokes are sent to the game window.

Directory: `%APPDATA%\XIVLauncher\pluginConfigs\Restocky\bridge\`

| File | Writer | Content |
|---|---|---|
| `heartbeat.json` | Restocky, every 5 s while loaded | `{"version":1,"plugin_version":"0.3.9.0","ts":<epoch s>,"logged_in":true,"world":"Cerberus","enabled":true}` |
| `inbox/<id>.json` | Gilhound | `{"version":1,"id":"<uuid4>","command":"/li Raiden market","created_ts":<epoch s>}` — written as `<id>.tmp` then renamed |
| `outbox/<id>.json` | Restocky | `{"version":1,"id":"<uuid4>","status":"ok"\|"rejected"\|"error","reason":"…","ts":<epoch s>}` |

The `ts` field in heartbeats and replies is UTC epoch seconds and may be fractional.

Rules (enforced by Restocky):
- Only `^/li [A-Za-z][A-Za-z' -]{1,30} market$` is accepted. Anything else → `rejected`.
- Requests older than 30 s → `rejected` ("expired").
- Not while in combat, a duty, a cutscene, between areas, or not logged in → `rejected` with the reason.
- At most one command per 2 s; one inbox file processed per tick, oldest first.
- Setting "Allow Gilhound travel commands" (default on) — when off, heartbeat has `"enabled":false` and every request is `rejected`.
- Inbox file deleted after processing; outbox files older than 10 min are pruned.

Gilhound treats the bridge as available when `heartbeat.json` is younger than
15 s and `enabled` is true; otherwise Go falls back to copying the command.
Gilhound waits up to 5 s for the outbox reply and shows its status.

## v2 addition: item search on market-board open (optional field)

Inbox requests may carry an optional `item` object:
`{"version":1,"id":"<uuid4>","command":"/li Raiden market","created_ts":<epoch s>,"item":{"id":50971,"hq":false,"name":"Ascendant Twine"}}`

- Restocky ≥ 0.3.10 accepts it (older versions ignore unknown fields).
- After the command succeeds, Restocky stores one **pending search** (item id,
  HQ flag, expires 10 min after the command, replaced by any newer Go).
- When the player opens the market board themselves (the item-search window
  appears) while the pending search is valid and the player is on the target
  world, Restocky searches that item and opens its listings, then clears the
  pending search. It never walks to or interacts with the market board.
- Heartbeat gains `"features":["travel","mb_search"]` so Gilhound can show
  whether item search is supported.
- `item.id` must be a positive integer; `name` is informational only.
