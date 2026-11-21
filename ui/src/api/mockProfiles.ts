import type { components } from "./types";

export const mockParams: Record<string, components["schemas"]["ScanParams"]> = {
  "Flip": {
    "windows": [
      {
        "preferred_roi": 40,
        "min_profit_amount": 1000,
        "min_desired_avg_ppu": 1500,
        "hours_ago": 24,
        "min_sales": 5
      }
    ],
    "history_entries": 80,
    "undercut_gil": 1,
    "quality": "both",
    "min_stack_size": 1,
    "filters": [
      0
    ],
    "region_wide": true,
    "include_vendor": false,
    "show_out_stock": false,
    "min_distinct_buyers": 0,
    "min_days_with_sales": 0,
    "min_units_day": 0.0,
    "max_data_age_h": null,
    "max_sale_age_h": null,
    "max_top_buyer_share": 1,
    "min_history_sales": 3,
    "home_listings": 50,
    "region_listings": 5,
    "home_entries": 100,
    "result_limit": 30,
    "preset": "flip",
    "home_world": "Cerberus",
    "buy_region": "Europe",
    "min_ppu": 0,
    "min_profit": 0,
    "buyer_tax": 0.05,
    "seller_tax": 0.05,
    "travel_cost": {},
    "sellable_days": 3
  },
  "High margin": {
    "windows": [
      {
        "preferred_roi": 40,
        "min_profit_amount": 5000,
        "min_desired_avg_ppu": 5000,
        "hours_ago": 24,
        "min_sales": 5
      },
      {
        "preferred_roi": 40,
        "min_profit_amount": 5000,
        "min_desired_avg_ppu": 5000,
        "hours_ago": 72,
        "min_sales": 3
      }
    ],
    "history_entries": 80,
    "undercut_gil": 1,
    "quality": "both",
    "min_stack_size": 1,
    "filters": [
      0
    ],
    "region_wide": true,
    "include_vendor": false,
    "show_out_stock": false,
    "min_distinct_buyers": 4,
    "min_days_with_sales": 3,
    "min_units_day": 0.0,
    "max_data_age_h": null,
    "max_sale_age_h": null,
    "max_top_buyer_share": 1,
    "min_history_sales": 4,
    "home_listings": 20,
    "region_listings": 8,
    "home_entries": 0,
    "result_limit": 60,
    "preset": "high_margin",
    "home_world": "Cerberus",
    "buy_region": "Europe",
    "min_ppu": 0,
    "min_profit": 12000,
    "buyer_tax": 0.05,
    "seller_tax": 0.05,
    "travel_cost": {},
    "sellable_days": 3
  },
  "Volume": {
    "windows": [
      {
        "preferred_roi": 40,
        "min_profit_amount": 3000,
        "min_desired_avg_ppu": 3000,
        "hours_ago": 24,
        "min_sales": 5
      },
      {
        "preferred_roi": 40,
        "min_profit_amount": 3000,
        "min_desired_avg_ppu": 3000,
        "hours_ago": 72,
        "min_sales": 3
      }
    ],
    "history_entries": 80,
    "undercut_gil": 1,
    "quality": "both",
    "min_stack_size": 1,
    "filters": [
      0
    ],
    "region_wide": true,
    "include_vendor": false,
    "show_out_stock": false,
    "min_distinct_buyers": 8,
    "min_days_with_sales": 4,
    "min_units_day": 2.5,
    "max_data_age_h": null,
    "max_sale_age_h": null,
    "max_top_buyer_share": 1,
    "min_history_sales": 4,
    "home_listings": 20,
    "region_listings": 8,
    "home_entries": 0,
    "result_limit": 60,
    "preset": "volume",
    "home_world": "Cerberus",
    "buy_region": "Europe",
    "min_ppu": 0,
    "min_profit": 6000,
    "buyer_tax": 0.05,
    "seller_tax": 0.05,
    "travel_cost": {},
    "sellable_days": 3
  },
  "Snipe": {
    "windows": [
      {
        "preferred_roi": 40,
        "min_profit_amount": 3000,
        "min_desired_avg_ppu": 3000,
        "hours_ago": 24,
        "min_sales": 5
      },
      {
        "preferred_roi": 40,
        "min_profit_amount": 3000,
        "min_desired_avg_ppu": 3000,
        "hours_ago": 72,
        "min_sales": 3
      },
      {
        "preferred_roi": 100,
        "min_profit_amount": 4000,
        "min_desired_avg_ppu": 5000,
        "hours_ago": 168,
        "min_sales": 3
      }
    ],
    "history_entries": 80,
    "undercut_gil": 1,
    "quality": "both",
    "min_stack_size": 1,
    "filters": [
      0
    ],
    "region_wide": true,
    "include_vendor": false,
    "show_out_stock": false,
    "min_distinct_buyers": 0,
    "min_days_with_sales": 0,
    "min_units_day": 0.0,
    "max_data_age_h": null,
    "max_sale_age_h": null,
    "max_top_buyer_share": 1,
    "min_history_sales": 3,
    "home_listings": 5,
    "region_listings": 12,
    "home_entries": 0,
    "result_limit": null,
    "preset": "snipe",
    "home_world": "Cerberus",
    "buy_region": "Europe",
    "min_ppu": 0,
    "min_profit": 0,
    "buyer_tax": 0.05,
    "seller_tax": 0.05,
    "travel_cost": {},
    "sellable_days": 3
  }
};
