# Europe listing freshness for snipe candidates

Measured 2026-11-21 UTC. The capture started at 11:17:33 UTC and used
the 73 item-and-quality candidates reached by the capped legacy snipe
scan. For each candidate, the Europe request asked for up to 12 listings
with the matching HQ flag and no sales entries. The analysis includes
Cerberus listings alongside the other worlds in Chaos and Light. It
covers the listing snapshot before the final snipe filters.
[Snipe capture index](../../tests/fixtures/market/snipe/index.json),
[Universalis current-data controller](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Controllers/V2/CurrentlyShownController.cs).

Subtract each listing's `lastReviewTime` from the Unix timestamp in
the fixture index. All 857 review timestamps were seconds in the
live capture. For a 13-digit millisecond timestamp, divide by 1,000
before subtracting. Every observation mapped to a known world and had a
positive, usable review timestamp. Percentiles use linear interpolation
over the ordered observations.
[Universalis listing schema](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Views/V1/ListingView.cs),
[Universalis data center mapping](https://universalis.app/api/v2/data-centers).

| Data center | Listings | P10 (h) | P25 (h) | P50 (h) | P75 (h) | P90 (h) | P95 (h) | P99 (h) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Chaos | 294 | 0.89 | 2.69 | 13.43 | 40.33 | 109.15 | 139.18 | 329.78 |
| Light | 563 | 0.92 | 3.55 | 14.43 | 24.13 | 53.78 | 85.99 | 301.96 |

| Data center | Fresh (<1 h) | Stale (1 to <6 h) | At least 6 h |
| --- | ---: | ---: | ---: |
| Chaos | 32 (10.9%) | 64 (21.8%) | 198 (67.3%) |
| Light | 71 (12.6%) | 75 (13.3%) | 417 (74.1%) |

Keep the plan's **under 1 hour** threshold for default Discord alerts and
**1 to under 6 hours** for stale results shown in the app. Both data
centers put their freshest tenth of listings near one hour. Hide
listings at least six hours old from the default alert feed, and show
the review age whenever a listing is displayed. Missing review times
should have unknown freshness.

This is one capture of price-ranked listings for a capped set of
Saddlebag candidates. Light supplied more listings in the returned
slice. The figures describe that slice and can change with the item
mix and time of day.
