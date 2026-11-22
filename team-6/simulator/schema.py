"""Column definitions taken from contracts/contracts.md (section 1).

Keep this file in sync with the contract; tests check the generator against it.
"""

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

DIMENSIONS = ["country", "platform", "category"]

# marketplace_hourly: (column, dtype kind) in contract order.
# dtype kind: "datetime", "str", "int", "float"
MARKETPLACE_HOURLY_COLUMNS = [
    ("timestamp", "datetime"),
    ("country", "str"),
    ("platform", "str"),
    ("category", "str"),
    ("sessions", "int"),
    ("searches", "int"),
    ("search_clicks", "int"),
    ("recommendation_impressions", "int"),
    ("recommendation_clicks", "int"),
    ("cart_adds", "int"),
    ("checkouts", "int"),
    ("payment_attempts", "int"),
    ("orders", "int"),
    ("refunds", "int"),
    ("revenue", "float"),
    ("ad_impressions", "int"),
    ("ad_clicks", "int"),
    ("ad_spend", "float"),
    ("active_listings", "int"),
    ("out_of_stock_views", "int"),
]

MARKETPLACE_HOURLY_COLUMN_NAMES = [name for name, _ in MARKETPLACE_HOURLY_COLUMNS]

# events: written by the scenario injector (L1 Person B). The baseline
# generator only writes an empty table with these columns.
EVENTS_COLUMNS = [
    "event_id",
    "start_time",
    "end_time",
    "event_type",
    "planned",
    "country",
    "platform",
    "category",
    "description",
    "expected_effect",
]

EVENT_TYPES = ["release", "campaign", "holiday", "data_delay"]
