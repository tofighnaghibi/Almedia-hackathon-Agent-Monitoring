"""The variable taxonomy served to the core: one declarative table, checked against Contracts/ (schema 2.0).

Every variable names its entity, kind, value schema and (for metrics) aggregation, population and time
semantics. Derived metrics are ratios with a closed formula_ast over other served variables, so the
relations between variables are declared, not inferred. Docs: docs/p0/VARIABLE_TAXONOMY.md (generated).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EventVar:
    variable_id: str
    event_type: str                         # canonical event type in the engine frame
    entity: str
    description: str
    fields: dict = field(default_factory=dict)    # field -> (Contracts EventFieldType, frame column)


@dataclass(frozen=True)
class MetricVar:
    variable_id: str
    entity: str
    aggregation: str                        # sum | count | mean | ratio | quantile
    dtype: str                              # real | integer | count | proportion
    unit: str | None
    population: str
    description: str
    formula: tuple[str, str] | None = None  # (numerator, denominator) for ratios
    identity_tolerance: float | None = None
    coverage: str = "full"


_ID = {"order": ("order_id", "text", "order_id"), "session": ("session_id", "text", "order_id")}


def _ev(vid, etype, entity, desc, **extra):
    k, t, col = _ID[entity]
    return EventVar(vid, etype, entity, desc, {k: (t, col), "region": ("category", "region"), **extra})


EVENTS = [
    _ev("order_placed", "order_created", "order", "an order is created (purchase time)",
        amount=("real", "amount"), promised_days=("real", "value")),
    EventVar("item_sold", "order_item", "order_item", "one line item of an order at purchase time",
             {"order_id": ("text", "order_id"), "seller_id": ("text", "seller_id"), "product_id": ("text", "product_id"),
              "region": ("category", "region"), "price": ("real", "amount"), "freight": ("real", "value")}),
    _ev("payment_approved", "payment_approved", "order", "the order's payment is approved", amount=("real", "amount")),
    _ev("handed_to_carrier", "handed_to_carrier", "order", "the seller hands the order to the carrier"),
    _ev("delivered", "delivered", "order", "the order is delivered to the customer", days_late=("real", "value")),
    _ev("survey_sent", "survey_sent", "order", "a satisfaction survey is sent"),
    _ev("review_score", "review_submitted", "order", "the customer answers the survey", score=("integer", "value")),
    _ev("session_started", "session_started", "session", "a shopping visit starts", device=("category", "device"),
        n_views=("integer", "value")),
    _ev("item_viewed", "item_viewed", "session", "an item is viewed from a results page", device=("category", "device"),
        position=("integer", "value")),
    _ev("cart_added", "cart_added", "session", "an item is added to the cart", device=("category", "device")),
    _ev("checkout_started", "checkout_started", "session", "checkout is started", device=("category", "device"),
        completed=("boolean", "value")),
]

METRICS = [
    MetricVar("orders_count", "order", "count", "count", "orders", "orders_created", "orders created in the window"),
    MetricVar("payments_count", "order", "count", "count", "orders", "payments_approved", "payment approvals in the window (by approval time)"),
    MetricVar("gmv", "order", "sum", "real", "BRL", "orders_created", "sum of order amounts in the window"),
    MetricVar("deliveries_count", "order", "count", "count", "orders", "deliveries", "deliveries in the window (by delivery time)"),
    MetricVar("late_deliveries_count", "order", "count", "count", "orders", "deliveries",
              "deliveries in the window that arrived after the promised date"),
    MetricVar("late_share", "order", "ratio", "proportion", None, "deliveries", "share of deliveries that were late",
              formula=("late_deliveries_count", "deliveries_count")),
    MetricVar("cohort_delivered_count", "order", "count", "count", "orders", "orders_created_cohort",
              "orders created in the window that were delivered by the data cut"),
    MetricVar("cohort_late_count", "order", "count", "count", "orders", "orders_created_cohort",
              "orders created in the window, delivered late by the data cut"),
    MetricVar("late_share_cohort", "order", "ratio", "proportion", None, "orders_created_cohort",
              "share of an order cohort delivered late (recent cohorts immature)", formula=("cohort_late_count", "cohort_delivered_count"),
              coverage="subset"),
    MetricVar("mean_promised_days", "order", "mean", "real", "days", "orders_created", "mean promised delivery time"),
    MetricVar("mean_delivery_days", "order", "mean", "real", "days", "orders_created_cohort", "mean purchase-to-delivery time",
              coverage="subset"),
    MetricVar("mean_review_score", "order", "mean", "real", "score 1-5", "surveys_answered", "mean survey score"),
    MetricVar("order_payment_ratio", "order", "ratio", "real", None, "orders_created",
              "accounting identity: every order is paid once (lags across window edges)", formula=("payments_count", "orders_count"),
              identity_tolerance=0.02),
    MetricVar("sessions_count", "session", "count", "count", "sessions", "sessions", "shopping visits started"),
    MetricVar("views_count", "session", "count", "count", "views", "sessions", "items viewed from results pages"),
    MetricVar("cart_adds_count", "session", "count", "count", "sessions", "sessions", "visits with a cart add"),
    MetricVar("checkouts_count", "session", "count", "count", "sessions", "sessions", "visits that started checkout"),
    MetricVar("cart_rate", "session", "ratio", "proportion", None, "sessions", "funnel: cart adds per visit",
              formula=("cart_adds_count", "sessions_count")),
    MetricVar("checkout_rate", "session", "ratio", "real", None, "sessions", "funnel: checkouts per cart add (buy-now skips the cart)",
              formula=("checkouts_count", "cart_adds_count")),
    MetricVar("completion_rate", "session", "ratio", "real", None, "sessions",
              "funnel: orders per checkout (about 1; can exceed 1 when a checkout and its order fall either side of a window edge)",
              formula=("orders_count", "checkouts_count")),
    MetricVar("views_per_session", "session", "ratio", "real", "views per session", "sessions", "views per visit",
              formula=("views_count", "sessions_count")),
]
EVENT_BY_ID = {e.variable_id: e for e in EVENTS}
METRIC_BY_ID = {m.variable_id: m for m in METRICS}
BASE_METRICS = [m.variable_id for m in METRICS if m.formula is None]           # computed by the engine
RATIOS = {m.variable_id: m for m in METRICS if m.formula is not None}           # computed from two served variables
