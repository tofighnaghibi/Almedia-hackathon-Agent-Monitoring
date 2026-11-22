"""Variable catalogue for an autonomous marketplace-monitoring agent, by taxonomy layer L0-L10.

One entry per variable *of the marketplace* (what a platform would log), in the four Contracts kinds:
metric, state, event, categorical. Datasets are sources behind a variable, never variables themselves.
Every entry builds into a valid Contracts VariableDefinition (tests/test_variable_catalog.py).

status:  served   - the ports serve it today (engine binding in mkt/taxonomy.py)
         planned  - a verified dataset backs it; the simulator layer that produces it is not built yet
         assumed  - no data; would be an assumption-labelled lever
L8 (ground truth) and per-field lineage (L10) are deliberately not variables: L8 is privileged, and
L10 travels inside every Observation (lineage, scope, clocks, data quality).
"""
from __future__ import annotations

from dataclasses import dataclass

from causal_core.contracts.common import SCHEMA_VERSION, EnvironmentScope, SourceAttestation
from causal_core.contracts.variables import (CategoricalValueSchema, CategoricalVariableDefinition, EventPayloadSchema, EventVariableDefinition,
                                 MetricDefinition, MetricTimeSemantics, NumericValueSchema, PopulationCoverage,
                                 StateVariableDefinition)

from . import taxonomy as TX

UF = ("AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO",
      "RR", "RS", "SC", "SE", "SP", "TO")


@dataclass(frozen=True)
class Var:
    layer: str
    variable_id: str
    kind: str                     # metric | state | event | categorical
    entity: str
    meaning: str
    why: str                      # why an autonomous monitoring agent needs it
    sources: str                  # primary (+ secondary) dataset and how it is used
    status: str                   # served | planned | assumed
    spec: dict                    # kind-specific schema (see build())


def M(layer, vid, entity, agg, dtype, unit, population, meaning, why, sources, status, formula=None, tol=None):
    return Var(layer, vid, "metric", entity, meaning, why, sources, status,
               {"agg": agg, "dtype": dtype, "unit": unit, "population": population, "formula": formula, "tol": tol})


def S(layer, vid, entity, meaning, why, sources, status, *, numeric=None, categories=None, ordered=False, cadence_s=None, unit=None):
    return Var(layer, vid, "state", entity, meaning, why, sources, status,
               {"numeric": numeric, "categories": categories, "ordered": ordered, "cadence_s": cadence_s, "unit": unit})


def E(layer, vid, entity, fields, meaning, why, sources, status):
    return Var(layer, vid, "event", entity, meaning, why, sources, status, {"fields": fields})


def C(layer, vid, entity, categories, meaning, why, sources, status, ordered=False, other=None):
    return Var(layer, vid, "categorical", entity, meaning, why, sources, status, {"categories": categories, "ordered": ordered, "other": other})


CATALOG: list[Var] = [
    # ---------------------------------------------------------------- L0 external environment
    S("L0", "public_holiday", "calendar_day", "national holiday on this day (name or 'none')",
      "separates 'the world changed' from 'we broke something' on holiday dips", "Holidays reference file (verified vs python-holidays)",
      "planned", categories=("none", "holiday", "optional_point"), cadence_s=86400),
    S("L0", "commercial_event", "calendar_day", "public commercial date (Black Friday, Christmas run-up, none)",
      "explains the 6.2x Black Friday peak without blaming ranking or ads", "Olist order series (fitted peak) + public calendar",
      "planned", categories=("none", "black_friday", "cyber_monday", "christmas_runup", "mothers_day"), cadence_s=86400),
    S("L0", "usd_brl", "economy", "USD/BRL exchange rate", "price-driven demand shifts for imported goods", "BCB SGS series 1 (daily)",
      "planned", numeric=("real", 0.0, None), cadence_s=86400, unit="BRL per USD"),
    S("L0", "selic_rate", "economy", "Selic policy rate", "credit-dependent purchase behaviour", "BCB SGS (daily)", "planned",
      numeric=("real", 0.0, None), cadence_s=86400, unit="% per year"),
    S("L0", "ipca_inflation", "economy", "monthly consumer inflation", "real vs nominal GMV change", "BCB SGS (monthly)", "planned",
      numeric=("real", None, None), cadence_s=2_592_000, unit="% per month"),
    S("L0", "logistics_disruption", "economy", "known national logistics disruption (e.g. the May 2018 truckers' strike)",
      "a delivery slowdown during a public strike is not a carrier or seller fault", "Public record reference file; visible in Olist",
      "planned", categories=("none", "active"), cadence_s=86400),
    # ---------------------------------------------------------------- L1 identity, scope, context
    C("L1", "buyer_region", "buyer", UF, "buyer's state (UF)", "regional slowdowns and Simpson-type mix shifts", "Olist customers", "served"),
    C("L1", "seller_region", "seller", UF, "seller's state (UF)", "seller-side regional issues; same-region vs cross-region shipping",
      "Olist sellers", "planned"),
    C("L1", "product_category", "product", ("bed_bath_table", "health_beauty", "sports_leisure", "computers_accessories", "furniture_decor",
                                            "housewares", "watches_gifts", "telephony", "auto", "toys", "cool_stuff", "other"),
      "product category (top categories + other)", "category-level demand and quality changes", "Olist products (English names)",
      "planned", other="other"),
    C("L1", "device", "session", ("desktop", "mobile", "tablet"), "device of a visit", "device-specific breakage (mobile checkout bug)",
      "Statcounter Brazil share + Google Analytics conversion ratio (transport cards TC-DV1, TC-DV2)", "served"),
    C("L1", "traffic_channel", "session", ("organic_search", "direct", "referral", "paid_search", "social", "display", "affiliates", "other"),
      "marketing channel of a visit", "a traffic-mix shift can look like a conversion drop", "Google Analytics channelGrouping (transferred mix)",
      "planned", other="other"),
    M("L1", "new_buyers_count", "buyer", "count", "count", "buyers", "first_orders", "buyers placing their first order",
      "acquisition vs retention changes", "Olist customer_unique_id", "planned"),
    # ---------------------------------------------------------------- L2 catalog, offers, competition
    S("L2", "offer_price", "offer", "current price of an offer", "price changes are the first suspect for demand shifts",
      "Olist item prices (levels) + M5 price-change frequency", "planned", numeric=("real", 0.0, None), unit="BRL"),
    E("L2", "price_changed", "offer", {"offer_id": "text", "old_price": "real", "new_price": "real"}, "an offer's price changed",
      "time-stamps the cause candidate for a demand change", "M5 weekly change probability 1.2% (transferred)", "planned"),
    S("L2", "offer_available", "offer", "offer can be bought (in stock and listed)", "stock-outs move sales to competitors",
      "Retailrocket availability flag over time (transferred)", "planned", categories=("available", "unavailable")),
    E("L2", "stockout_started", "offer", {"offer_id": "text", "seller_id": "text"}, "an offer went out of stock",
      "explains a seller's sales drop without blaming ranking", "Retailrocket availability transitions", "planned"),
    M("L2", "active_offers_count", "offer", "count", "count", "offers", "offers_listed", "offers listed and available",
      "supply-side capacity", "derived from offer_available", "planned"),
    M("L2", "multi_seller_products_count", "product", "count", "count", "products", "products_listed", "products offered by 2+ sellers at once",
      "competition intensity (Buy-Box-like allocation)", "Olist (123 simultaneous cases; rest synthetic, labelled)", "planned"),
    E("L2", "search_submitted", "session", {"session_id": "text", "query_id": "text", "results": "integer"}, "a search was run",
      "search breakage and zero-result spikes", "ESCI queries (bridged to the synthetic catalog)", "planned"),
    M("L2", "zero_result_search_share", "session", "ratio", "proportion", None, "searches", "share of searches with no results",
      "search index or relevance regressions", "ESCI relevance labels (bridged)", "planned", formula=("zero_result_searches_count", "searches_count")),
    M("L2", "searches_count", "session", "count", "count", "searches", "searches", "searches run", "denominator for search quality",
      "ESCI (bridged)", "planned"),
    M("L2", "zero_result_searches_count", "session", "count", "count", "searches", "searches", "searches that returned nothing",
      "numerator for search quality", "ESCI (bridged)", "planned"),
    # ---------------------------------------------------------------- L3 seller health
    S("L3", "seller_status", "seller", "seller lifecycle state", "seller churn shrinks supply before it shows in orders",
      "Olist Funnel (entry) + Olist sales (exit, censored)", "planned", categories=("onboarding", "active", "inactive_30d")),
    E("L3", "seller_onboarded", "seller", {"seller_id": "text", "segment": "category", "origin": "category"}, "a seller closed a deal and joined",
      "supply growth attribution", "Olist Marketing Funnel (842 deals, 380 join to sales)", "planned"),
    M("L3", "active_sellers_count", "seller", "count", "count", "sellers", "sellers", "sellers with a sale in the last 30 days",
      "supply health", "Olist sales", "planned"),
    M("L3", "mean_handling_hours", "seller", "mean", "real", "hours", "orders_created", "approval to carrier handover",
      "seller-side fulfilment degradation vs carrier problems", "Olist (approval and carrier timestamps)", "planned"),
    S("L3", "platform_fee_rate", "platform", "commission charged to sellers", "fee changes change seller behaviour",
      "none public (assumption A-10)", "assumed", numeric=("proportion", 0.0, 1.0)),
    # ---------------------------------------------------------------- L4 demand and discovery
    M("L4", "sessions_count", "session", "count", "count", "sessions", "sessions", "shopping visits started", "top of the funnel",
      "anchored to Olist orders x Retailrocket visits per buyer (TC-RR1)", "served"),
    M("L4", "views_count", "session", "count", "count", "views", "sessions", "items viewed", "browsing depth", "Retailrocket (TC-RR1)", "served"),
    M("L4", "cart_adds_count", "session", "count", "count", "sessions", "sessions", "visits with a cart add", "funnel stage",
      "Retailrocket (TC-RR1)", "served"),
    M("L4", "checkouts_count", "session", "count", "count", "sessions", "sessions", "visits that started checkout", "funnel stage",
      "Retailrocket + Taobao timing (TC-RR1, TC-TB1)", "served"),
    M("L4", "cart_rate", "session", "ratio", "proportion", None, "sessions", "cart adds per visit", "funnel localisation",
      "derived", "served", formula=("cart_adds_count", "sessions_count")),
    M("L4", "checkout_rate", "session", "ratio", "real", None, "sessions", "checkouts per cart add", "funnel localisation", "derived",
      "served", formula=("checkouts_count", "cart_adds_count")),
    M("L4", "completion_rate", "session", "ratio", "real", None, "sessions", "orders per checkout", "checkout and payment breakage",
      "derived", "served", formula=("orders_count", "checkouts_count")),
    M("L4", "views_per_session", "session", "ratio", "real", "views per session", "sessions", "views per visit", "engagement shifts",
      "derived", "served", formula=("views_count", "sessions_count")),
    E("L4", "item_viewed", "session", {"session_id": "text", "region": "category", "device": "category", "position": "integer"},
      "an item was opened from a results page", "exposure denominators", "Expedia random-order position curve (TC-EX1)", "served"),
    E("L4", "session_started", "session", {"session_id": "text", "region": "category", "device": "category", "n_views": "integer"},
      "a visit started", "traffic level and device mix", "Retailrocket + Statcounter + GA (TC-RR1, TC-DV1, TC-DV2)", "served"),
    E("L4", "cart_added", "session", {"session_id": "text", "region": "category", "device": "category"}, "an item went to the cart",
      "funnel stage", "Retailrocket (TC-RR1)", "served"),
    E("L4", "checkout_started", "session", {"session_id": "text", "region": "category", "device": "category", "completed": "boolean"},
      "checkout started", "payment and checkout faults", "Taobao timing (TC-TB1)", "served"),
    M("L4", "impressions_count", "session", "count", "count", "impressions", "sessions", "result slots shown", "exposure denominator",
      "Expedia result lists (TC-EX1)", "planned"),
    M("L4", "ctr_position_1", "session", "ratio", "proportion", None, "sessions", "click rate at result position 1",
      "ranking regressions show as a position-curve change", "Expedia random order (TC-EX1)", "planned",
      formula=("clicks_position_1_count", "impressions_position_1_count")),
    M("L4", "clicks_position_1_count", "session", "count", "count", "clicks", "sessions", "clicks on position 1", "numerator", "Expedia", "planned"),
    M("L4", "impressions_position_1_count", "session", "count", "count", "impressions", "sessions", "position-1 slots shown", "denominator",
      "Expedia", "planned"),
    E("L4", "recommendation_shown", "session", {"session_id": "text", "item_id": "text", "position": "integer", "propensity": "real"},
      "a recommendation was shown, with the logging policy's propensity", "off-policy evaluation of recommender changes",
      "Open Bandit (uniform + BTS propensities)", "planned"),
    M("L4", "repeat_buyer_share", "buyer", "ratio", "proportion", None, "buyers", "share of orders from returning buyers",
      "retention changes", "Olist 3.1% + Instacart interval shape", "planned", formula=("repeat_orders_count", "orders_count")),
    M("L4", "repeat_orders_count", "buyer", "count", "count", "orders", "orders_created", "orders by returning buyers", "numerator",
      "Olist", "planned"),
    E("L4", "ad_exposure", "session", {"session_id": "text", "campaign_id": "text", "treated": "boolean"}, "a sponsored placement was shown",
      "paid vs organic attribution", "Criteo uplift (randomised; transferred lift)", "planned"),
    # ---------------------------------------------------------------- L5 transactions and fulfilment
    *[Var("L5" if m.variable_id not in ("sessions_count",) else "L4", m.variable_id, "metric", m.entity, m.description,
          "core business outcome or its declared components", "Olist (replay and chain)", "served",
          {"agg": m.aggregation, "dtype": m.dtype, "unit": m.unit, "population": m.population, "formula": m.formula, "tol": m.identity_tolerance})
      for m in TX.METRICS if m.entity == "order" and m.variable_id != "mean_review_score"],
    *[E("L5", e.variable_id, e.entity, {k: t for k, (t, _) in e.fields.items()}, e.description,
        "order lifecycle step (time-stamped)", "Olist (replay and chain)", "served")
      for e in TX.EVENTS if e.entity in ("order", "order_item") and e.variable_id not in ("survey_sent", "review_score")],
    C("L5", "payment_type", "order", ("credit_card", "boleto", "voucher", "debit_card", "not_defined"), "how the order was paid",
      "payment-method-specific failures (boleto delays)", "Olist payments", "planned", other="not_defined"),
    S("L5", "order_status", "order", "order lifecycle status", "stuck orders (approved, never shipped)", "Olist order status",
      "planned", categories=("created", "approved", "invoiced", "processing", "shipped", "delivered", "canceled", "unavailable")),
    M("L5", "cancellations_count", "order", "count", "count", "orders", "orders_created", "orders canceled",
      "cancellation spikes (stock or payment problems)", "Olist (625 canceled + 609 unavailable)", "planned"),
    M("L5", "returns_count", "order", "count", "count", "orders", "orders_delivered", "returned orders", "quality problems after delivery",
      "UCI 'C' invoices (transferred; Olist has no returns)", "planned"),
    # ---------------------------------------------------------------- L6 trust and feedback
    *[E("L6", e.variable_id, e.entity, {k: t for k, (t, _) in e.fields.items()}, e.description, "satisfaction signal and its timing",
        "Olist reviews", "served") for e in TX.EVENTS if e.variable_id in ("survey_sent", "review_score")],
    M("L6", "mean_review_score", "order", "mean", "real", "score 1-5", "surveys_answered", "mean survey score",
      "satisfaction follows lateness (4.29 on time vs 1.71 late)", "Olist reviews", "served"),
    M("L6", "survey_response_share", "order", "ratio", "proportion", None, "surveys_sent", "surveys answered / sent",
      "who leaves reviews (MNAR); falling response hides dissatisfaction", "Olist (~99%) + Coat selection shape",
      "planned", formula=("surveys_answered_count", "surveys_sent_count")),
    M("L6", "surveys_sent_count", "order", "count", "count", "surveys", "surveys_sent", "surveys sent", "denominator", "Olist", "planned"),
    M("L6", "surveys_answered_count", "order", "count", "count", "surveys", "surveys_sent", "surveys answered", "numerator", "Olist", "planned"),
    M("L6", "review_answer_lag_p50_hours", "order", "quantile", "real", "hours", "surveys_answered", "median survey answer delay",
      "outcome maturity: recent weeks are incomplete", "Olist (median 40 h)", "planned"),
    E("L6", "review_text_submitted", "order", {"order_id": "text", "score": "integer", "language": "category", "text": "text"},
      "free-text review (pt-BR)", "complaint themes behind a score drop", "Olist review comments (41% have text; pooled by score x delivery)",
      "planned"),
    M("L6", "flagged_reviews_count", "review", "count", "count", "reviews", "reviews", "reviews flagged by the platform's spam filter",
      "review manipulation campaigns", "YelpChi / Amazon fraud graph (detector shape; true labels stay privileged)", "planned"),
    # ---------------------------------------------------------------- L7 platform and infrastructure
    *[M("L7", f"{svc}.{st}", "service", agg, dtype, unit, f"{svc}_server_spans", f"{st} of {svc}", "infrastructure-caused symptoms",
        "Astronomy Shop telemetry (redacted)", "served")
      for svc in ("frontend", "checkout", "payment", "cart", "product-catalog")
      for st, agg, dtype, unit in (("requests", "count", "count", "requests"), ("errors", "count", "count", "requests"),
                                   ("error_rate", "mean", "proportion", None), ("latency_p95_ms", "quantile", "real", "ms"))],
    S("L7", "ranking_policy_version", "platform", "ranking model version in production", "a ranking release explains exposure shifts",
      "platform release log (emulator lever)", "planned", categories=("v1", "v2", "v3")),
    E("L7", "deployment", "service", {"service": "category", "version": "text"}, "a service was deployed",
      "change-point candidates for incidents", "Astronomy Shop image versions (emulator release log)", "planned"),
    M("L7", "experiment_conversion", "buyer", "mean", "proportion", None, "experiment_units", "conversion per arm of a randomised test",
      "the agent's own experiments (served as assignment_outcomes)", "Open Bandit / Criteo assignment shapes", "planned"),
    # ---------------------------------------------------------------- L9 observation and measurement
    M("L9", "ingestion_lag_p95_s", "pipeline", "quantile", "real", "s", "records", "95th percentile of available_at - event_time",
      "late data looks like a drop until it arrives", "observation model (assumed A-11) + Olist batch evidence", "planned"),
    M("L9", "late_corrections_count", "pipeline", "count", "count", "records", "records", "records replaced by a later revision",
      "revisions change history after the fact", "observation model (assumed A-12)", "planned"),
    M("L9", "out_of_order_records_count", "pipeline", "count", "count", "records", "records",
      "lifecycle steps logged in an impossible order (e.g. handed to carrier before approval)",
      "measurement anomalies vs real process changes", "Olist (1,359 handovers before approval)", "planned"),
    M("L9", "purchases_without_view_share", "pipeline", "ratio", "proportion", None, "orders_created",
      "purchases with no logged prior view", "logging gaps in the web stream", "Retailrocket (11.9%)", "planned",
      formula=("purchases_without_view_count", "orders_count")),
    M("L9", "purchases_without_view_count", "pipeline", "count", "count", "orders", "orders_created", "purchases with no logged view",
      "numerator", "Retailrocket", "planned"),
    S("L9", "event_schema_version", "pipeline", "schema version of the event stream", "a schema change explains a field going null",
      "emulator lever (assumed)", "assumed", categories=("v1", "v2")),
]

EXCLUDED = [  # dataset fields considered and NOT turned into monitoring variables
    ("L8", "fraud labels (YelpChi, Amazon fraud graph)", "ground truth: privileged; the agent only sees the platform's own flags"),
    ("L8", "fault flag states (Astronomy Shop flagd)", "hidden-fault ground truth; redacted from telemetry by design (E3)"),
    ("L8", "true lateness, latent quality, twin outcomes, intervention log", "evaluator-only (privileged plane)"),
    ("L10", "per-field provenance, coverage, freshness", "already carried by every Observation (lineage, scope, clocks, dq); not variables"),
    ("—", "Criteo f0-f11, Open Bandit user features", "anonymised model features with no marketplace meaning"),
    ("—", "Expedia hotel star rating, length of stay, competitor rates", "lodging-specific; only the position curve transfers"),
    ("—", "Hillstrom spend, Instacart day-of-week, Taobao 'fav'", "mechanism shapes for other variables, not marketplace signals here"),
    ("—", "M5 SNAP days", "US food-stamp calendar: no Brazilian equivalent"),
    ("—", "NAB / LogHub series", "anomaly shapes for synthetic telemetry only (fallback), not variables of this marketplace"),
    ("—", "ESCI product titles, brand, colour", "catalog enrichment (bridged attributes), not time-varying variables"),
]


def build(v: Var, scope: EnvironmentScope):
    """The Contracts VariableDefinition for a catalogue entry."""
    from causal_core.contracts.variables import FormulaBinOp, FormulaRef
    base = dict(schema_version=SCHEMA_VERSION, variable_id=v.variable_id, scope=scope, entity_ref=v.entity, description=v.meaning,
                provenance=SourceAttestation(source_id="mkt-catalog", method="adapter_declared"),
                extensions={"mkt:layer": v.layer, "mkt:status": v.status, "mkt:sources": v.sources[:200]})
    s = v.spec
    if v.kind == "metric":
        f = s["formula"]
        return MetricDefinition(**base, kind="metric",
                                value_schema=NumericValueSchema(type="numeric", dtype=s["dtype"], minimum=0.0,
                                                                maximum=1.0 if s["dtype"] == "proportion" else None),
                                unit=s["unit"], aggregation_kind=s["agg"],
                                population_coverage=PopulationCoverage(population_id=s["population"], coverage="unknown"),
                                time_semantics=MetricTimeSemantics(grain="1w", window="tumbling", timezone="America/Sao_Paulo"),
                                formula_ast=(FormulaBinOp(node="binop", op="div", left=FormulaRef(node="ref", variable_id=f[0]),
                                                          right=FormulaRef(node="ref", variable_id=f[1])) if f else None),
                                identity_tolerance=s["tol"])
    if v.kind == "state":
        vs = (NumericValueSchema(type="numeric", dtype=s["numeric"][0], minimum=s["numeric"][1], maximum=s["numeric"][2])
              if s["numeric"] else CategoricalValueSchema(type="categorical", categories=s["categories"], ordered=s["ordered"]))
        return StateVariableDefinition(**base, kind="state", value_schema=vs, unit=s["unit"], sampling_cadence_s=s["cadence_s"],
                                       irregular_sampling=s["cadence_s"] is None)
    if v.kind == "event":
        ident = next(k for k in s["fields"] if k.endswith("_id")) if any(k.endswith("_id") for k in s["fields"]) else next(iter(s["fields"]))
        return EventVariableDefinition(**base, kind="event", occurrence_time="event_time",
                                       value_schema=EventPayloadSchema(type="event", fields=s["fields"], identity_fields=(ident,)))
    return CategoricalVariableDefinition(**base, kind="categorical",
                                         value_schema=CategoricalValueSchema(type="categorical", categories=s["categories"],
                                                                             ordered=s["ordered"], other_code=s["other"]))
