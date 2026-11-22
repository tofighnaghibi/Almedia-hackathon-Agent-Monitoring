"""Business domains for the console's top bar, and which metric belongs to which.

This is business knowledge kept on the UI side for now. It would be better as a `domain` field on each metric in
brain/metrics/*.yaml (suggested to B); until then a metric that is not listed here shows up under "Other".
"""
from __future__ import annotations

DOMAINS = [
    ("traffic", "Traffic", ["sessions"]),
    ("search", "Search", ["searches", "search_clicks", "search_ctr"]),
    ("recommendations", "Recommendations", ["feed_impressions", "feed_clicks", "feed_ctr"]),
    ("cart", "Cart & checkout", ["product_views", "views_per_session", "add_to_cart", "add_to_cart_rate", "checkouts", "checkout_rate"]),
    ("payments", "Payments", ["payment_attempts", "payment_attempt_rate", "payment_successes", "payment_success_rate"]),
    ("orders", "Orders", ["orders", "order_rate", "conversion_rate", "refunds"]),
    ("revenue", "Revenue", ["contribution", "gmv", "aov", "marketplace_fees", "take_rate", "discounts", "discount_rate"]),
    ("ads", "Ads", ["ad_spend", "ad_clicks", "ad_impressions", "ad_load", "ad_ctr", "ad_cpc"]),
    ("inventory", "Inventory", ["stockouts", "stockout_rate"]),
]


def as_json() -> list[dict]:
    return [{"id": i, "label": label, "metrics": m} for i, label, m in DOMAINS]
