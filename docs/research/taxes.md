# Seller tax on retainer sales

Checked 2026-11-21 UTC. The seller lost approximately **5%** of the gross
listing price in the matched sample, independently of the buyer surcharge.
Square Enix documents reduced market tax rates and the separate fee on
purchases, so the observed seller rate should be tied to the retainer's
market rather than treated as universal.
[Square Enix 7.0 reduced rates](https://eu.finalfantasyxiv.com/lodestone/topics/detail/c807875c5f8f7529887c86d2955f709eae0231ef/),
[Square Enix 5.2 purchase fee](https://na.finalfantasyxiv.com/lodestone/topics/detail/cd927661a344f9996c8adf16fbb412570d4b7cab).

## Local comparison

- **CashFlow and AllaganMarket.** Match by item and quantity within five
  minutes, treating the AllaganMarket local timestamps as UTC+03:00 and
  keeping only CashFlow rows with a single match. The 17 pairs matched
  distinct AllaganMarket rows. The ratio
  `RetainerSales.Price / (SoldItems.UnitPrice × Quantity)` ranged from
  **0.95000 to 0.95027**, with median **0.95001**. Both multi-unit matches
  were near 0.95 by this calculation. Multiplying CashFlow `Price` by
  `Quantity` again would overstate these proceeds. CashFlow labels the
  field **Paid** and adds `Price` directly in its sales UI.
  [CashFlow descriptor](https://github.com/NightmareXIV/CashFlow/blob/main/CashFlow/Data/LegacyDescriptors/RetainerSaleDescriptor.cs),
  [CashFlow sales UI](https://github.com/NightmareXIV/CashFlow/blob/main/CashFlow/Gui/BaseTabs/TabRetainerSales.cs),
  [AllaganMarket SoldItem](https://github.com/Critical-Impact/AllaganMarket/blob/main/AllaganMarket/Models/SoldItem.cs).
- **CashFlow balance deltas.** The four `GilRecords` snapshots yielded
  three intervals for the same character, with 31, 16 and 8 sales in
  `(prior snapshot, next snapshot]`. Retainer balance changes divided by
  `sum(Price × Quantity)` were **0.00000**, **0.10676** and **−0.00933**.
  Dividing by `sum(Price)` yielded **0.00000**, **0.14757** and **−0.31140**.
  Player balances also changed in all three intervals. With pooled activity
  and no isolated before-and-after balance, the snapshots cannot establish
  the tax rate on an individual sale.
  [CashFlow balance record](https://github.com/NightmareXIV/CashFlow/blob/main/CashFlow/Data/SqlDescriptors/GilRecordSqlDescriptor.cs).
- **Additional check.** The one Restocky row sourced from an in-game sale
  message with a recorded listing price had a received-to-listed ratio of
  **0.95001**. All 75 AllaganMarket sale rows had an encoded `TaxRate` of
  zero. The AllaganMarket tracker estimates tax while matching disappearing
  listings against gil changes and creates a `SoldItem` from the listing.
  Use its recorded listing price as the gross comparison in this sample.
  [AllaganMarket sale tracker](https://github.com/Critical-Impact/AllaganMarket/blob/main/AllaganMarket/Services/SaleTrackerService.cs).

## Universalis listing tax

Listing `total` is `pricePerUnit × quantity`. Listing `tax` is the
**purchase** surcharge, calculated as
`floor(pricePerUnit × quantity × 0.05)`. For the ledger, treat the observed
CashFlow sale `Price` as the net stack proceeds, and apply seller tax only
when estimating net proceeds from a gross listing price.
[Listing view](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Views/V1/ListingView.cs),
[listing calculation](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Controllers/CurrentlyShownControllerBase.cs),
[tax calculation](https://github.com/Universalis-FFXIV/Universalis/blob/v2/src/Universalis.Application/Util.cs).
