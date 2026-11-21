"""Shared market, scan, ledger and settings contracts."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, computed_field, field_serializer, model_validator

EpochSeconds = Annotated[int, Field(strict=True, ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
GilCost = Annotated[int, Field(ge=0)]


class World(BaseModel):
    id: int = Field(gt=0, description="World ID.")
    name: str = Field(description="World name.")
    dc: str = Field(description="Data centre name.")


class DataCenter(BaseModel):
    name: str = Field(description="Data centre name.")
    region: str = Field(description="Market region name.")
    worlds: list[World] = Field(description="Worlds in the data centre.")


class ItemRef(BaseModel):
    icon_url: str | None = None
    universalis_url: str | None = None
    saddlebag_url: str | None = None
    id: int = Field(gt=0, description="Item ID.")
    name: str = Field(description="Item display name.")
    hq: bool = Field(description="Whether this is the high-quality variant.")


class Listing(BaseModel):
    world: str = Field(description="World where the item is listed.")
    ppu: int = Field(gt=0, description="Listed gil per unit before tax.")
    qty: int = Field(gt=0, description="Units in the listing.")
    hq: bool = Field(description="Whether the listing is for high-quality units.")
    retainer_hash: str = Field(description="Anonymised listing retainer identifier.")
    last_review_ts: EpochSeconds = Field(description="Last review in UTC epoch seconds.")
    retainer_name: str | None = Field(
        default=None, exclude=True, repr=False, description="Runtime-only retainer name."
    )


class Sale(BaseModel):
    ppu: int = Field(gt=0, description="Sale price in gil per unit.")
    qty: int = Field(gt=0, description="Units sold.")
    hq: bool = Field(description="Whether the sale was for high-quality units.")
    ts: EpochSeconds = Field(description="Sale time in UTC epoch seconds.")
    buyer_hash: str = Field(description="Anonymised buyer identifier.")


class VolumeProof(BaseModel):
    units_per_day: float = Field(ge=0, description="Average daily units sold over seven days.")
    distinct_buyers_7d: int = Field(ge=0, description="Unique buyers in the past seven days.")
    last_sale_age_h: float | None = Field(
        description="Hours since the latest sale, or null if no sale is known."
    )
    top_buyer_share: Fraction = Field(description="Share of units bought by the largest buyer.")
    days_with_sales: int = Field(ge=0, le=7, description="Days with sales in the seven-day window.")


class ScanPreset(StrEnum):
    FLIP = "flip"
    HIGH_MARGIN = "high_margin"
    VOLUME = "volume"
    SAFE_FLIP = "safe_flip"
    SNIPE = "snipe"


class SnipeRule(BaseModel):
    max_home_median_ratio: Fraction = Field(
        default=0.5, description="Maximum listing price as a fraction of the home-world median."
    )
    min_profit: GilCost = Field(default=5_000, description="Minimum net profit in gil per unit.")
    min_distinct_buyers_7d: int = Field(
        default=3, ge=0, description="Minimum seven-day buyer count."
    )
    max_last_sale_age_h: float = Field(default=24, gt=0, description="Exclusive sale age limit.")
    max_top_buyer_share: Fraction = Field(
        default=0.5, description="Maximum share held by the largest buyer (default 0.5, or 50%)."
    )


class FreshnessTier(BaseModel):
    name: str = Field(description="Display name for the freshness tier.")
    max_age_h: float = Field(gt=0, description="Exclusive upper limit on listing age in hours.")
    send_discord: bool = Field(description="Whether this tier can trigger Discord notifications.")


class BridgeStatus(BaseModel):
    status: Literal["available", "disabled", "not_running", "incompatible"]
    plugin_version: str | None = None
    world: str | None = None


class BridgeResult(BaseModel):
    status: Literal["ok", "rejected", "error", "unavailable"]
    reason: str


class BridgeGo(BaseModel):
    world: str


class CandidateWindow(BaseModel):
    preferred_roi: int = Field(default=40, ge=0, description="Requested return on investment (%).")
    min_profit_amount: GilCost = Field(default=1000, description="Minimum candidate profit (gil).")
    min_desired_avg_ppu: GilCost = Field(default=1500, description="Minimum average unit price.")
    hours_ago: int = Field(default=24, gt=0, description="Candidate sales lookback in hours.")
    min_sales: int = Field(default=5, ge=0, description="Minimum sales in this window.")


class ScanParams(BaseModel):
    large_buy_gil: GilCost | None = Field(
        default=None, description="Large-buy taxed outlay threshold."
    )
    min_net_multiple: float = Field(default=1, ge=0, description="Minimum net profit / outlay.")
    snipe_rule: SnipeRule | None = Field(default=None, description="Rules for snipe strategy.")
    freshness_tiers: list[FreshnessTier] | None = Field(
        default=None, description="Snipe alert tiers. Omit to use configured tiers."
    )

    windows: list[CandidateWindow] = Field(
        default_factory=list, min_length=1, description="Saddlebag searches to combine."
    )
    quality: Literal["both", "hq", "nq"] = Field(default="both", description="Qualities to search.")
    min_stack_size: int = Field(default=1, ge=1, description="Minimum candidate stack size.")
    filters: list[int] = Field(default_factory=lambda: [0], description="Saddlebag filter IDs.")
    region_wide: bool = Field(default=True, description="Search the full buy region.")
    include_vendor: bool = Field(default=False, description="Include vendor items.")
    show_out_stock: bool = Field(default=False, description="Include out-of-stock candidates.")
    min_distinct_buyers: int = Field(default=0, ge=0, description="Minimum unique weekly buyers.")
    min_days_with_sales: int = Field(default=0, ge=0, le=7, description="Minimum active sale days.")
    min_units_day: float = Field(default=0, ge=0, description="Minimum average daily volume.")
    max_data_age_h: float | None = Field(default=None, gt=0, description="Listing age limit (h).")
    max_sale_age_h: float | None = Field(default=None, gt=0, description="Latest sale age limit.")
    max_top_buyer_share: Fraction = Field(default=1, description="Largest allowed buyer fraction.")
    history_entries: int = Field(
        default=80, ge=1, description="Maximum sales to fetch across fourteen days from history."
    )
    undercut_gil: GilCost = Field(
        default=1, description="Gil below lowest home listing for snipes."
    )
    min_history_sales: int = Field(default=3, gt=0, description="Minimum weekly sales to price.")
    home_listings: int = Field(default=50, gt=0, le=100, description="Home listings fetched.")
    region_listings: int = Field(default=5, gt=0, le=100, description="Regional listings fetched.")
    home_entries: int = Field(default=100, ge=0, le=100, description="Home sale entries fetched.")
    result_limit: int | None = Field(default=30, gt=0, description="Maximum returned rows.")

    preset: ScanPreset = Field(default=ScanPreset.FLIP, description="Scan strategy.")
    home_world: str = Field(pattern=r"\S", description="World where items will be resold.")
    buy_region: str = Field(pattern=r"\S", description="Market region to search for purchases.")
    min_ppu: GilCost = Field(default=0, description="Minimum purchase price in gil per unit.")
    min_profit: GilCost = Field(default=0, description="Minimum net profit in gil per unit.")
    buyer_tax: Fraction = Field(default=0.05, description="Buyer tax as a fraction of the price.")
    seller_tax: Fraction = Field(description="Seller tax as a fraction of the price.")
    travel_cost: dict[str, GilCost] = Field(
        default_factory=dict, description="Travel cost in gil keyed by data centre name."
    )
    sellable_days: float = Field(default=3, gt=0, description="Days of sales used to cap quantity.")

    @model_validator(mode="before")
    @classmethod
    def strategy_defaults(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        preset = values.get("preset", "flip")
        flip, margin = preset == "flip", preset == "high_margin"
        volume = preset in ("volume", "safe_flip")
        floor = 1500 if flip else 5000 if margin else 3000
        windows = [(floor, 1000 if flip else floor, 24, 5, 40)]
        if not flip:
            windows.append((floor, floor, 72, 3, 40))
        if preset == "snipe":
            windows.append((5000, 4000, 168, 3, 100))
        defaults = dict(
            windows=[
                dict(
                    min_desired_avg_ppu=values.get("min_ppu") or price,
                    min_profit_amount=values.get("min_profit") or gain,
                    hours_ago=hours,
                    min_sales=sales,
                    preferred_roi=roi,
                )
                for price, gain, hours, sales, roi in windows
            ],
            min_profit=12000 if margin else 6000 if volume else 0,
            min_distinct_buyers=4 if margin else 8 if volume else 0,
            min_days_with_sales=3 if margin else 4 if volume else 0,
            min_units_day=2.5 if volume else 0,
            min_history_sales=4 if margin or volume else 3,
            home_listings=50 if flip else 5 if preset == "snipe" else 20,
            region_listings=5 if flip else 12 if preset == "snipe" else 8,
            home_entries=100 if flip else 0,
            result_limit=30 if flip else None if preset == "snipe" else 60,
        )
        if preset == "safe_flip":
            defaults.update(max_sale_age_h=24, max_top_buyer_share=0.5, max_data_age_h=1)
        return defaults | values


class ScanRow(BaseModel):
    ask_exit_ppu: float | None = None
    ask_profit_per_unit: float | None = None
    ask_profit_capped: float | None = None
    ask_below_floor: bool | None = None
    home_lowest_units: int | None = None
    median_profit_per_unit: float | None = None
    whole_stack_outlay: int | None = None
    buy_stacks: list[int] | None = None
    large_buy_below_multiple: bool | None = None
    units_sold_7d: int | None = None
    sales_7d: int | None = None
    average_sale_size: float | None = None
    min_sale_price: int | None = None
    max_sale_price: int | None = None
    hq_sale_share: float | None = None
    home_listing_count: int | None = None
    distinct_sellers: int | None = None
    rival_stock: int | None = None
    units_below_sell: int | None = None
    nearby_listings: int | None = None
    nearby_units: int | None = None
    second_buy_price: int | None = None
    next_listing_gap: int | None = None
    roi_pct: float | None = None
    capital_needed: float | None = None
    profit_per_day: float | None = None
    days_to_sell: float | None = None
    trend_pct: float | None = None
    daily_units_14d: list[int] | None = None
    item: ItemRef = Field(description="Item and quality variant.")
    buy_world: str = Field(description="World where the purchase is available.")
    buy_dc: str = Field(description="Data centre where the purchase is available.")
    buy_ppu: int = Field(gt=0, description="Purchase price in gil per unit before tax.")
    buy_qty_at_price: int = Field(gt=0, description="Units available at the purchase price.")
    home_median_7d: float = Field(gt=0, description="Home-world seven-day median gil per unit.")
    home_lowest: int | None = Field(description="Lowest current home-world gil per unit, if known.")
    sellable_qty: int = Field(ge=0, description="Estimated units sellable within the sales window.")
    profit_per_unit: float = Field(description="Estimated net gil profit per unit after costs.")
    total_profit_capped: float = Field(description="Estimated net profit for the sellable units.")
    est_wait_days: float | None = Field(description="Estimated days to sell, if calculable.")
    volume_proof: VolumeProof = Field(description="Home-world sales evidence.")
    data_age_h: float = Field(ge=0, description="Age of the purchase listing data in hours.")


class QuoteRequest(BaseModel):
    item_id: int = Field(gt=0)
    hq: bool
    buy_world: str = Field(min_length=1, max_length=80)
    home_world: str = Field(min_length=1, max_length=80)
    history_entries: int = Field(default=80, ge=1, le=10000)


class QuoteSnapshot(BaseModel):
    price: int | None
    qty: int
    stacks: list[int]
    age_h: float | None
    home_lowest: int | None
    home_median: float | None


class RejectedCandidate(BaseModel):
    item: ItemRef
    reasons: list[str]
    buy_world: str | None = None


class ScanResult(BaseModel):
    rejected: list[RejectedCandidate] = Field(default_factory=list)
    params: ScanParams = Field(description="Parameters applied to the scan.")
    rows: list["SnipeHit | ScanRow"] = Field(description="Matching purchase opportunities.")
    started_ts: EpochSeconds = Field(description="Scan start in UTC epoch seconds.")
    finished_ts: EpochSeconds = Field(description="Scan completion in UTC epoch seconds.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal scan warnings.")


class SnipeStatus(BaseModel):
    running: bool = False
    next_run_ts: EpochSeconds | None = None
    last_run_ts: EpochSeconds | None = None
    fresh: int = 0
    stale: int = 0
    notified: int = 0


class SnipeHit(ScanRow):
    tier: FreshnessTier = Field(description="Freshness tier of the purchase listing.")
    listing_key: str = Field(
        description="Stable listing identifier for notification deduplication."
    )
    first_seen_ts: EpochSeconds = Field(description="First detection in UTC epoch seconds.")
    notified_ts: EpochSeconds | None = Field(
        default=None, description="Last notification in UTC epoch seconds, if sent."
    )


class RetainerScanParams(BaseModel):
    retainer_name: str = Field(min_length=1, description="Retainer name to match at runtime.")
    world: str | None = Field(default=None, description="Search world. Defaults to the home world.")
    buy_region: str | None = Field(
        default=None, description="Comparison region. Defaults to the configured buy region."
    )


class RetainerItemRow(BaseModel):
    item: ItemRef = Field(description="Item and quality variant.")
    their_ppu: int = Field(gt=0, description="Retainer's listed gil per unit.")
    qty: int = Field(gt=0, description="Units in the retainer's listing.")
    data_age_h: float = Field(ge=0, description="Age of the retainer listing data in hours.")
    cheapest_world: str | None = Field(description="World of the cheapest regional listing.")
    cheapest_dc: str | None = Field(description="Data centre of the cheapest regional listing.")
    cheapest_ppu: int | None = Field(description="Cheapest regional listing price in gil per unit.")
    profit_qty: int | None = Field(
        default=None, gt=0, description="Units bought in the cheapest stack sharing one trip cost."
    )
    cheapest_qty: int | None = Field(description="Units in the cheapest regional listing.")
    gap_pct: float | None = Field(description="Discount from the retainer price in percent.")
    profit_per_unit: float | None = Field(
        description="Net gil per unit from buying regionally and selling at the retainer price."
    )


class RetainerScanResult(BaseModel):
    params: RetainerScanParams = Field(description="Parameters applied to the retainer scan.")
    rows: list[RetainerItemRow] = Field(description="Retainer listings and their regional prices.")
    items_scanned: int = Field(ge=0, description="Number of marketable items checked.")
    duration_s: float = Field(ge=0, description="Elapsed scan time in seconds.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal scan warnings.")


class SourceName(StrEnum):
    CASHFLOW = "cashflow"
    ALLAGAN_MARKET = "allagan_market"
    ALLAGAN_TOOLS = "allagan_tools"
    RESTOCKY = "restocky"


class SourceStatus(BaseModel):
    name: SourceName = Field(description="Plugin data source.")
    found: bool = Field(description="Whether the source files were found.")
    recognised: bool = Field(description="Whether the source schema was recognised.")
    last_update_ts: EpochSeconds | None = Field(
        default=None, description="Last source update in UTC epoch seconds, if known."
    )
    warning: str | None = Field(default=None, description="Non-fatal source warning, if any.")


class LedgerEventKind(StrEnum):
    BUY = "buy"
    SELL = "sell"
    LIST = "list"
    UNLIST = "unlist"
    TRADE_IN = "trade_in"
    OPENING = "opening"


class LedgerEvent(BaseModel):
    kind: LedgerEventKind = Field(description="Type of ledger event.")
    item: ItemRef = Field(description="Item and quality variant affected by the event.")
    qty: int = Field(gt=0, description="Number of units affected.")
    ppu: GilCost | None = Field(description="Gil per unit, or null if unknown or inapplicable.")
    ts: EpochSeconds = Field(description="Event time in UTC epoch seconds.")
    source: SourceName | None = Field(description="Plugin source, or null for manual entries.")


class SaleDetails(BaseModel):
    retainer: str | None = None
    gross: GilCost | None = None
    first_listed: EpochSeconds | None = None
    reprices: int | None = None
    own_undercuts: int | None = None
    rivals: dict[str, int] = Field(default_factory=dict)


class RealisedSale(SaleDetails):
    item: ItemRef
    ts: EpochSeconds
    qty: int
    net: GilCost
    sources: list[SourceName]
    fifo_cost: GilCost | None
    unknown_qty: int
    known_profit: int
    roi_pct: float | None
    purchase_days: float | None
    listing_days: float | None

    @computed_field
    @property
    def buy_ppu(self) -> float | None:
        return self.fifo_cost / self.qty if self.fifo_cost is not None else None

    @computed_field
    @property
    def gross_ppu(self) -> float | None:
        return self.gross / self.qty if self.gross is not None else None

    @computed_field
    @property
    def net_ppu(self) -> float:
        return self.net / self.qty

    @computed_field
    @property
    def profit(self) -> int | None:
        return None if self.unknown_qty else self.known_profit


class SalesByItem(BaseModel):
    item: ItemRef
    sales: int = 0
    qty: int = 0
    net: int = 0
    known_profit: int = 0
    unknown_qty: int = 0
    profit_per_slot_day: float | None = None


class Position(BaseModel):
    lowest_list_price: int | None = None
    first_listed: EpochSeconds | None = None
    undercut_count: int | None = None
    home_lowest: int | None = None
    home_rank: int | None = None
    home_observed_ts: EpochSeconds | None = None
    item: ItemRef = Field(description="Item and quality variant held.")
    known_capital: int = Field(default=0, ge=0, description="Cost of remaining known FIFO lots.")
    qty_open: int = Field(ge=0, description="Units still held, including listed units.")
    avg_cost: float | None = Field(description="Average open cost in gil, if known.")
    cost_known: bool = Field(description="Whether all open units have a known cost basis.")
    listed_qty: int = Field(ge=0, description="Open units currently listed.")
    listing_count: int = Field(
        default=0, ge=0, description="Stacks in the current listing snapshot."
    )
    realised_profit: int = Field(description="Net realised gil profit for sales with known cost.")


class PnLSummary(BaseModel):
    realised_profit: int = Field(description="Net realised gil profit for sales with known cost.")
    sales_proceeds: GilCost = Field(description="Gil proceeds from all recorded sales after tax.")
    known_cost_of_goods_sold: GilCost = Field(
        description="FIFO cost for sold units with known cost."
    )
    unknown_cost_qty_sold: int = Field(ge=0, description="Sold units with unknown cost basis.")


class UserSettings(BaseModel):
    large_buy_gil: GilCost | None = Field(
        default=None, description="Large-buy taxed outlay threshold."
    )
    min_net_multiple: float = Field(default=1, ge=0, description="Minimum net profit / outlay.")
    history_days: int = Field(default=90, ge=1, le=3650)
    snipe_rule: SnipeRule = Field(default_factory=SnipeRule)
    home_world: str = Field(description="Default world for resale and retainer scans.")
    buy_region: str = Field(description="Default market region for purchases.")
    auto_snipe_enabled: bool = True
    discord_coin_emoji: str = Field(default="🪙", min_length=1, max_length=80)
    scan_interval_min: int = Field(
        default=5, gt=0, le=240, description="Minutes between snipe scans."
    )
    discord_notifier: bool = Field(
        default=False, description="Whether Discord delivery is enabled."
    )
    webhook_url: SecretStr | None = Field(
        default=None, description="Discord webhook URL. Returned as a fixed mask when supplied."
    )
    dedup_cooldown_h: float = Field(
        default=6, ge=0, description="Hours before a listing can resend."
    )
    freshness_tiers: list[FreshnessTier] = Field(
        default_factory=lambda: [
            FreshnessTier(name="fresh", max_age_h=1, send_discord=True),
            FreshnessTier(name="stale", max_age_h=6, send_discord=False),
        ],
        description="Freshness thresholds in ascending order of age.",
    )
    travel_cost: dict[str, GilCost] = Field(
        default_factory=dict, description="Travel cost in gil keyed by data centre name."
    )

    @field_serializer("webhook_url")
    def mask_webhook_url(self, value: SecretStr | None) -> str | None:
        return "**********" if value else None


class AppInfo(BaseModel):
    latest_version: str | None = Field(default=None, description="Newer release, when available.")
    version: str = Field(description="Installed Gilhound version.")
    update_available: bool = Field(description="Whether a newer release is available.")
    updates_supported: bool = Field(
        default=True, description="Whether this layout supports updates."
    )
    update_checked_at: int | None = Field(
        default=None, description="Last check in UTC epoch seconds."
    )
    update_check_error: str | None = Field(default=None, description="Last release feed failure.")
