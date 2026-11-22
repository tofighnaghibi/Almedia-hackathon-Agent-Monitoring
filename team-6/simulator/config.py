"""Configuration for the baseline marketplace generator.

All the "business physics" of the simulated marketplace lives here, so it can be
tuned without touching the generator code. Defaults give roughly 1.2M sessions
and ~38k orders per day across 4 countries x 3 platforms x 4 categories.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimConfig:
    start: str = "2026-10-01"  # first day (UTC midnight)
    days: int = 28
    seed: int = 42

    countries: list[str] = field(default_factory=lambda: ["DE", "UK", "FR", "US"])
    platforms: list[str] = field(default_factory=lambda: ["android", "ios", "web"])
    categories: list[str] = field(default_factory=lambda: ["electronics", "fashion", "home", "beauty"])

    # --- traffic -----------------------------------------------------------
    # Mean sessions per hour for a country with scale 1.0, averaged over the day.
    # A cell gets base * country_scale * platform_share * category_share, then
    # the seasonal multipliers.
    base_country_sessions_per_hour: float = 12500.0
    country_scale: dict[str, float] = field(
        default_factory=lambda: {"DE": 1.0, "UK": 0.8, "FR": 0.6, "US": 1.4}
    )
    # Platform share of traffic, per country (each row sums to 1).
    platform_share: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "DE": {"android": 0.42, "ios": 0.30, "web": 0.28},
            "UK": {"android": 0.35, "ios": 0.40, "web": 0.25},
            "FR": {"android": 0.45, "ios": 0.28, "web": 0.27},
            "US": {"android": 0.33, "ios": 0.45, "web": 0.22},
        }
    )
    # Category share of traffic (sums to 1).
    category_share: dict[str, float] = field(
        default_factory=lambda: {"electronics": 0.25, "fashion": 0.35, "home": 0.25, "beauty": 0.15}
    )
    # Fixed UTC offsets used for the local-time daily curve (no DST, on purpose:
    # keeps the baseline simple and reproducible).
    utc_offset_hours: dict[str, int] = field(
        default_factory=lambda: {"DE": 1, "UK": 0, "FR": 1, "US": -5}
    )
    # Day-of-week multiplier, Monday=0 .. Sunday=6 (normalised to mean 1).
    weekday_factor: list[float] = field(
        default_factory=lambda: [0.94, 0.95, 0.97, 1.00, 1.03, 1.08, 1.12]
    )
    # Extra weekend lift per category (multiplied on Sat/Sun).
    weekend_category_lift: dict[str, float] = field(
        default_factory=lambda: {"electronics": 1.00, "fashion": 1.06, "home": 1.08, "beauty": 1.02}
    )
    daily_growth: float = 0.001  # +0.1% traffic per day

    # Noise: lognormal sigma of a day-level shock per country (shared by all
    # segments in that country), a day-level shock per segment, and an
    # hour-level shock per row. Poisson/binomial sampling noise comes on top.
    noise_day_country: float = 0.03
    noise_day_segment: float = 0.03
    noise_hour: float = 0.04
    # Sigma of the day-level wobble applied to conversion rates.
    noise_rate_day: float = 0.01

    # --- funnel rates ------------------------------------------------------
    searches_per_session: dict[str, float] = field(
        default_factory=lambda: {"android": 1.3, "ios": 1.3, "web": 1.6}
    )
    search_ctr: dict[str, float] = field(  # search_clicks / searches
        default_factory=lambda: {"electronics": 0.32, "fashion": 0.38, "home": 0.35, "beauty": 0.36}
    )
    rec_impressions_per_session: dict[str, float] = field(
        default_factory=lambda: {"android": 3.2, "ios": 3.4, "web": 2.4}
    )
    rec_ctr: dict[str, float] = field(  # recommendation_clicks / impressions
        default_factory=lambda: {"electronics": 0.040, "fashion": 0.055, "home": 0.045, "beauty": 0.050}
    )
    ad_impressions_per_session: float = 1.6
    ad_ctr: dict[str, float] = field(  # ad_clicks / ad_impressions
        default_factory=lambda: {"electronics": 0.018, "fashion": 0.024, "home": 0.020, "beauty": 0.022}
    )
    # Product views that come from browsing / deep links (not in the table).
    browse_views_per_session: float = 0.6
    add_to_cart_rate: dict[str, float] = field(  # cart_adds / in-stock product views
        default_factory=lambda: {"electronics": 0.045, "fashion": 0.065, "home": 0.055, "beauty": 0.075}
    )
    checkout_rate: dict[str, float] = field(  # checkouts / cart_adds
        default_factory=lambda: {"android": 0.50, "ios": 0.55, "web": 0.58}
    )
    payment_attempt_rate: dict[str, float] = field(  # payment_attempts / checkouts
        default_factory=lambda: {"android": 0.88, "ios": 0.90, "web": 0.91}
    )
    payment_success_rate: dict[str, float] = field(  # orders / payment_attempts
        default_factory=lambda: {"android": 0.950, "ios": 0.968, "web": 0.960}
    )
    refund_rate: dict[str, float] = field(  # refunds / orders
        default_factory=lambda: {"electronics": 0.040, "fashion": 0.085, "home": 0.050, "beauty": 0.030}
    )

    # --- money -------------------------------------------------------------
    aov_eur: dict[str, float] = field(  # average order value per category
        default_factory=lambda: {"electronics": 180.0, "fashion": 58.0, "home": 76.0, "beauty": 34.0}
    )
    country_price_factor: dict[str, float] = field(
        default_factory=lambda: {"DE": 1.00, "UK": 1.05, "FR": 0.98, "US": 1.10}
    )
    # Shape of the per-order value distribution (Gamma); higher = less spread.
    order_value_shape: float = 3.0
    cpc_eur: dict[str, float] = field(
        default_factory=lambda: {"electronics": 0.85, "fashion": 0.48, "home": 0.58, "beauty": 0.44}
    )
    country_cpc_factor: dict[str, float] = field(
        default_factory=lambda: {"DE": 1.00, "UK": 1.10, "FR": 0.90, "US": 1.30}
    )

    # --- catalog and inventory ---------------------------------------------
    products_per_category: int = 150
    sellers: int = 40
    first_party_share: float = 0.35  # share of products the marketplace buys wholesale
    popularity_zipf_s: float = 0.9  # popularity ~ 1 / rank**s
    units_per_order: float = 1.3
    lead_time_days: dict[str, int] = field(  # purchase order lead time by fulfilment
        default_factory=lambda: {"1p": 5, "3p": 3}
    )
    lead_time_jitter_days: int = 2  # deliveries arrive up to this many days late
    safety_stock_days: float = 1.5
    order_cover_days: float = 14.0  # a purchase order covers this many days of demand
    # Sigma of the day-level wobble in actual product demand (lognormal).
    demand_noise: float = 0.25
    # Sigma of the per-product error in the purchasing forecast (lognormal);
    # under-forecast products are the ones that run out.
    forecast_error: float = 0.20
    top_seller_share: float = 0.10  # top 10% of products by popularity ...
    top_seller_error_factor: float = 0.3  # ... have 30% of the forecast error
