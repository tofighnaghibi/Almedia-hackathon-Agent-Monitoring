"""Contract-valid samples for every catalogue variable (mkt/variable_catalog.py), from the most honest source:

  emulator   simulator output through the contract port            origin synthetic_generated · role SYNTHETIC_GENERATED
  olist      real Olist data replayed offline                       origin original            · role SANDBOX_OBSERVED
  external   rows of another dataset, shown as-is (transfer source) origin external_study      · role EXTERNAL_STUDY
  card       synthesised from a stitch card (or a named assumption) origin synthetic_generated · role SYNTHETIC_GENERATED
  telemetry  real Astronomy Shop spans (saved storefront run)       origin original            · role SANDBOX_OBSERVED

Every sample is a Contracts Observation (+ EvidenceRecord); builders only choose values, never the format.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import date, datetime, timedelta, timezone

import numpy as np

from causal_core.contracts.capabilities import GetEventBatch, GetSeries
from causal_core.contracts.common import SCHEMA_VERSION, Clock, EnvironmentScope, ProvenanceLineage, TimeWindow
from causal_core.contracts.evidence import EvidenceRecord
from causal_core.contracts.observations import (AssignedUnit, AssignmentOutcomes, CategoricalValue, DataQuality, EventBatch, EventRecord,
                                    Observation, ObservationClocks, StateSample, StateTrajectory, TimePoint, TimeSeries)

from .fitters import ITEMS, O, ORD, RAW, REV, db, q

U = timezone.utc
CUT = datetime(2018, 5, 1, tzinfo=U)                 # data cut for every sample
W0, W1 = datetime(2018, 1, 1, 3, tzinfo=U), datetime(2018, 3, 5, 3, tzinfo=U)
ROLE = {"original": "SANDBOX_OBSERVED", "synthetic_generated": "SYNTHETIC_GENERATED", "external_study": "EXTERNAL_STUDY"}
CARDS = pathlib.Path(__file__).resolve().parents[2] / "docs/stitch/cards"


def card(cid: str) -> dict:
    return json.loads((CARDS / f"{cid}.json").read_text())


def sha(o) -> str:
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()


def rng(seed: int, vid: str) -> np.random.Generator:
    return np.random.default_rng(int(sha([seed, vid])[:12], 16))


def scope(kind: str, env_id: str, gen: str | None = None) -> EnvironmentScope:
    return EnvironmentScope(environment_id=env_id, environment_kind=kind, data_cut=CUT, generator_version=gen)


OLIST = scope("offline_replay", "olist-offline-replay-2018")
CARD_SCOPE = lambda seed: scope("emulator", f"stitch-samples-s{seed}", f"stitch-cards-{sha(sorted(p.name for p in CARDS.glob('*.json')))[:12]}")
EXTERNAL = lambda ds: scope("offline_replay", f"external-{ds}")


def observation(vid, payload, origin, sc: EnvironmentScope, source_id, mechanisms=(), observed=None):
    body = payload.model_dump(mode="json")
    lin = ProvenanceLineage(source_id=source_id, origin_kind=origin, lineage_root_id=f"{source_id}:{sc.environment_id}",
                            generator_version=sc.generator_version if origin == "synthetic_generated" else None, content_hash=sha(body))
    obs_at = observed or CUT
    o = Observation(schema_version=SCHEMA_VERSION, observation_id=f"obs:{sha([vid, body])[:24]}", variable_id=vid, payload=payload,
                    source_id=source_id, record_key=f"sample:{vid}", revision=1, lineage=lin, scope=sc,
                    clocks=ObservationClocks(observed_at=Clock(quality="KNOWN", value=obs_at), available_at=Clock(quality="KNOWN", value=CUT),
                                             as_of=CUT, source_timezone="America/Sao_Paulo"),
                    dq=DataQuality(missingness="declared", selection="declared"), observation_mechanisms=tuple(mechanisms))
    ev = EvidenceRecord(schema_version="2.0", evidence_id=f"ev:{o.observation_id}", lineage=lin, scope=sc, provenance_role=ROLE[origin], content_hash=lin.content_hash)
    return o, ev


def utc(t: datetime) -> datetime:
    """UTC on the wire (contracts v3.2 §2.2): naive = UTC; aware = converted."""
    return t.astimezone(U) if t.tzinfo else t.replace(tzinfo=U)


def ts(points) -> TimeSeries:
    pts = sorted({t: v for t, v in points}.items())
    return TimeSeries(shape="time_series", points=tuple(TimePoint(t=utc(t), value=None if v is None else float(v))
                                                      for t, v in pts))


def traj(samples) -> StateTrajectory:
    s = sorted({utc(t): v for t, v in samples}.items())
    return StateTrajectory(shape="state_trajectory", samples=tuple(StateSample(t=t, state=v) for t, v in s))


def events(rows, vid) -> EventBatch:
    return EventBatch(shape="event_batch", events=tuple(
        EventRecord(event_id=f"{vid}:{i}", occurred_at=Clock(quality="KNOWN", value=utc(t)), attributes=a)
        for i, (t, a) in enumerate(rows)))


def weekly_olist(c, sql_value: str, where: str = "true", time_col: str = "order_purchase_timestamp", frm: str = ORD):
    rows = q(c, f"""select date_trunc('week', {time_col}) w, {sql_value} from {frm} where {where}
                    and {time_col} >= '2018-01-01' and {time_col} < '2018-03-05' group by 1 order by 1""")
    return ts([(r[0], r[1]) for r in rows])


# ================================================================== builders: variable_id -> (payload, origin, scope, source, mechanisms)
def build_all(seed: int, emu_port, tele_port) -> dict:
    c = db(4)
    R = lambda vid: rng(seed, vid)
    cs = CARD_SCOPE(seed)
    B = {}

    # ---------------- served by the emulator (contract port answers directly)
    def from_port(port, vid, kind):
        if kind == "metric":
            r = port.query(GetSeries(schema_version="2.0", request_id=f"s:{vid}", variables=(vid,), scope=port.scope, data_cut=CUT,
                                     window=TimeWindow(start=W0, end=W1), purpose="contract sample", request="get_series", grain="1w"))
        else:
            r = port.query(GetEventBatch(schema_version="2.0", request_id=f"s:{vid}", variables=(vid,), scope=port.scope, data_cut=CUT,
                                         window=TimeWindow(start=datetime(2018, 1, 8, 3, tzinfo=U), end=datetime(2018, 1, 9, 3, tzinfo=U)),
                                         purpose="contract sample", request="get_event_batch", max_rows=50_000, max_events=50_000))
        if r.status != "OK":
            return f"port refused: {r.reason}"
        o = r.observations[0]
        if kind == "event" and len(o.payload.events) > 50:
            o = o.model_copy(update={"payload": EventBatch(shape="event_batch", events=o.payload.events[:50])})
        return o, port.evidence_record(o)

    B["__from_port__"] = from_port

    # ---------------- L0
    hol = {r[0]: r[2] for r in q(c, f"select date, name, type from read_csv('{RAW}/holidays/holidays_br_2016_2018.csv') where year(date)=2018")}
    days = [date(2018, 1, 1) + timedelta(d) for d in range(0, 120)]
    B["public_holiday"] = (traj([(datetime.combine(d, datetime.min.time()), "holiday" if hol.get(d) == "national_holiday" else
                                  "optional_point" if d in hol else "none") for d in days]), "original", OLIST, "holidays-reference", ("mech:public_calendar",))
    bf = date(2017, 11, 24)
    B["commercial_event"] = (traj([(datetime(2017, 11, 20) + timedelta(d), "black_friday" if datetime(2017, 11, 20).date() + timedelta(d) == bf else
                                    "cyber_monday" if datetime(2017, 11, 20).date() + timedelta(d) == date(2017, 11, 27) else
                                    "christmas_runup" if (datetime(2017, 11, 20) + timedelta(d)).month == 12 and (datetime(2017, 11, 20) + timedelta(d)).day <= 24
                                    else "none") for d in range(40)]), "original", OLIST, "public-calendar", ("mech:public_calendar_rule",))
    macro = pathlib.Path.home() / "data/mkt/stitch/bridges/L0-macro.parquet"
    for vid, series in (("usd_brl", "usd_brl"), ("selic_rate", "selic_target_pct"), ("ipca_inflation", "ipca_monthly_pct")):
        rows = q(c, f"select data, valor from '{macro}' where series='{series}' and data between '2017-10-01' and '2018-04-30' order by 1")
        B[vid] = (ts([(datetime.combine(r[0], datetime.min.time()), r[1]) for r in rows]), "original", OLIST, "bcb-sgs", ("mech:joined_by_date",))
    s = card("L0-shock")["estimate"]["window"]
    st = date.fromisoformat(s[0]); en = date.fromisoformat(s[1])
    B["logistics_disruption"] = (traj([(datetime.combine(st + timedelta(d), datetime.min.time()), "active" if st + timedelta(d) <= en else "none")
                                       for d in range(-7, 21)]), "original", OLIST, "public-record", ("mech:documented_event_window",))

    # ---------------- L1
    reg = q(c, f"select seller_state from read_csv('{O}/olist_sellers_dataset.csv') using sample 40 rows (reservoir, {seed})")
    B["seller_region"] = ([CategoricalValue(shape="categorical", category=r[0]) for r in reg], "original", OLIST, "olist", ())
    cats = q(c, f"""select coalesce(category, 'other') from '{_canon()}/products.parquet' using sample 40 rows (reservoir, {seed})""")
    from ..variable_catalog import CATALOG
    known = next(v for v in CATALOG if v.variable_id == "product_category").spec["categories"]
    B["product_category"] = ([CategoricalValue(shape="categorical", category=r[0] if r[0] in known else "other") for r in cats], "original", OLIST, "olist", ())
    ch = q(c, f"select channelGrouping from read_csv('{RAW}/ga/train.csv', all_varchar=true) using sample 40 rows (reservoir, {seed})")
    cmap = {"Organic Search": "organic_search", "Direct": "direct", "Referral": "referral", "Paid Search": "paid_search", "Social": "social",
            "Display": "display", "Affiliates": "affiliates"}
    B["traffic_channel"] = ([CategoricalValue(shape="categorical", category=cmap.get(r[0], "other")) for r in ch], "external_study",
                            EXTERNAL("ga"), "google-analytics-sample", ("mech:transferred_mix",))
    B["new_buyers_count"] = (weekly_olist(c, "count(*)", frm=f"""(select min(order_purchase_timestamp) order_purchase_timestamp from {ORD} o
                             join read_csv('{O}/olist_customers_dataset.csv') cu using(customer_id) group by customer_unique_id)"""),
                             "original", OLIST, "olist", ())

    # ---------------- L2
    top = q(c, f"""select product_id from {ITEMS} group by 1 having count(distinct price) > 3 order by count(*) desc limit 1""")[0][0]
    pr = q(c, f"""select o.order_purchase_timestamp, i.price from {ITEMS} i join {ORD} o using(order_id) where product_id='{top}' order by 1""")
    B["offer_price"] = (traj([(r[0], float(r[1])) for r in pr]), "original", OLIST, "olist", ("mech:sold_price_as_offer_price",))
    chg, prev = [], None
    for t, p in pr:
        if prev is not None and p != prev:
            chg.append((t, {"offer_id": top[:40], "old_price": float(prev), "new_price": float(p)}))
        prev = p
    B["price_changed"] = (events(chg[:50], "price_changed"), "original", OLIST, "olist", ("mech:sold_price_changes",))
    so = float(np.mean(card("L2-stock")["estimate"]["stockout_snapshot_share"]))
    r = R("offer_available")
    B["offer_available"] = (traj([(datetime(2018, 1, 1) + timedelta(days=7 * k), "unavailable" if r.random() < so else "available") for k in range(10)]),
                            "synthetic_generated", cs, "stitch-card:L2-stock", ("mech:card_L2-stock",))
    B["stockout_started"] = (events([(datetime(2018, 1, 1) + timedelta(hours=int(h)), {"offer_id": f"offer-{k}", "seller_id": f"seller-{k % 7}"})
                                     for k, h in enumerate(sorted(R("stockout").integers(0, 24 * 60, 12)))], "stockout_started"),
                             "synthetic_generated", cs, "stitch-card:L2-stock", ("mech:card_L2-stock",))
    act = weekly_olist(c, "count(distinct product_id)", frm=f"(select * from {ITEMS} i join {ORD} o using(order_id))")
    B["active_offers_count"] = (ts([(p.t, round(p.value * (1 - so))) for p in act.points]), "synthetic_generated", cs, "stitch-card:L2-stock",
                                ("mech:sold_products_x_availability",))
    B["multi_seller_products_count"] = (weekly_olist(c, "count(*) filter (where s > 1)", frm=f"""(select date_trunc('week', o.order_purchase_timestamp)
                                        order_purchase_timestamp, product_id, count(distinct seller_id) s from {ITEMS} i join {ORD} o using(order_id) group by 1, 2)"""),
                                        "original", OLIST, "olist", ())
    br = pathlib.Path.home() / "data/mkt/stitch/bridges/L2-search.parquet"
    qs = q(c, f"select query_id, count(*) n, count(*) filter (where esci_label in ('E','S')) rel from '{br}' group by 1 order by hash(query_id + {seed}) limit 30")
    B["search_submitted"] = (events([(datetime(2018, 1, 8, 10) + timedelta(minutes=3 * i), {"session_id": f"s{i}", "query_id": str(r_[0]), "results": int(r_[1])})
                                     for i, r_ in enumerate(qs)], "search_submitted"), "external_study", EXTERNAL("esci"), "esci", ("mech:bridged_queries",))
    zero = q(c, f"select avg(case when rel = 0 then 1.0 else 0 end) from (select query_id, count(*) filter (where esci_label in ('E','S')) rel from '{br}' group by 1)")[0][0]
    sess = from_port(emu_port, "sessions_count", "metric")
    if isinstance(sess, tuple):
        searches = [(p.t, round(p.value * 0.35)) for p in sess[0].payload.points]
        B["searches_count"] = (ts(searches), "synthetic_generated", cs, "stitch-card:L2-search", ("mech:assumed_0.35_searches_per_session",))
        B["zero_result_searches_count"] = (ts([(t, round(v * zero)) for t, v in searches]), "synthetic_generated", cs, "stitch-card:L2-search",
                                           ("mech:esci_queries_without_relevant_result",))
        B["zero_result_search_share"] = (ts([(t, round(v * zero) / v if v else None) for t, v in searches]), "synthetic_generated", cs,
                                         "stitch-card:L2-search", ("mech:esci_queries_without_relevant_result",))
        views = from_port(emu_port, "views_count", "metric")[0].payload.points
        B["impressions_count"] = (ts([(p.t, p.value * 10) for p in views]), "synthetic_generated", cs, "stitch-card:L4-exposure",
                                  ("mech:10_slots_per_results_page",))
        rel1 = card("L4-exposure")["estimate"]["rel_ctr"]
        share1 = 1 / sum(rel1)
        B["clicks_position_1_count"] = (ts([(p.t, round(p.value * share1)) for p in views]), "synthetic_generated", cs, "stitch-card:L4-exposure",
                                        ("mech:expedia_position_curve",))
        B["impressions_position_1_count"] = (ts([(p.t, p.value) for p in views]), "synthetic_generated", cs, "stitch-card:L4-exposure",
                                             ("mech:one_position_1_slot_per_page",))
        B["ctr_position_1"] = (ts([(p.t, round(p.value * share1) / p.value if p.value else None) for p in views]), "synthetic_generated", cs,
                               "stitch-card:L4-exposure", ("mech:expedia_position_curve",))

    # ---------------- L3
    deals = q(c, f"""select d.won_date, d.seller_id, coalesce(d.business_segment,'unknown'), coalesce(m.origin,'unknown')
                     from read_csv('{RAW}/olist_funnel/olist_closed_deals_dataset.csv') d
                     left join read_csv('{RAW}/olist_funnel/olist_marketing_qualified_leads_dataset.csv') m using(mql_id) order by 1 limit 40""")
    B["seller_onboarded"] = (events([(r_[0], {"seller_id": r_[1], "segment": r_[2], "origin": r_[3]}) for r_ in deals], "seller_onboarded"),
                             "original", OLIST, "olist-funnel", ())
    ex = float(np.mean(card("L3-exit")["estimate"]["share_inactive_within_180d_after_cohort"]))
    r = R("seller_status")
    states, cur = [], "onboarding"
    for k in range(12):
        cur = "active" if cur == "onboarding" else ("inactive_30d" if cur == "active" and r.random() < ex / 6 else cur)
        states.append((datetime(2018, 1, 1) + timedelta(days=30 * k), cur))
    B["seller_status"] = (traj(states), "synthetic_generated", cs, "stitch-card:L3-exit", ("mech:card_L3-exit_monthly_hazard",))
    B["active_sellers_count"] = (weekly_olist(c, "count(distinct seller_id)", frm=f"(select * from {ITEMS} i join {ORD} o using(order_id))"),
                                 "original", OLIST, "olist", ("mech:sellers_with_a_sale_in_the_week",))
    B["mean_handling_hours"] = (weekly_olist(c, "avg(epoch(order_delivered_carrier_date - order_approved_at)/3600)",
                                             where="order_delivered_carrier_date > order_approved_at"), "original", OLIST, "olist", ())
    B["platform_fee_rate"] = (ts([(datetime(2018, 1, 1) + timedelta(days=7 * k), 0.15) for k in range(9)]), "synthetic_generated", cs,
                              "assumption-A-10", ("mech:assumption_A-10_fee_midpoint",))

    # ---------------- L4
    ob = q(c, f"select timestamp, item_id, position, propensity_score from read_csv('{RAW}/open_bandit/random/all.csv') limit 30")
    B["recommendation_shown"] = (events([(datetime.fromisoformat(str(r_[0])), {"session_id": f"ob{i}", "item_id": str(r_[1]), "position": int(r_[2]),
                                                                            "propensity": float(r_[3])}) for i, r_ in enumerate(ob)], "recommendation_shown"),
                                 "external_study", EXTERNAL("open-bandit"), "open-bandit", ("mech:logged_policy_propensity",))
    rep = f"""(select o.order_purchase_timestamp, row_number() over (partition by cu.customer_unique_id order by o.order_purchase_timestamp) k
               from {ORD} o join read_csv('{O}/olist_customers_dataset.csv') cu using(customer_id))"""
    B["repeat_orders_count"] = (weekly_olist(c, "count(*) filter (where k > 1)", frm=rep), "original", OLIST, "olist", ())
    B["repeat_buyer_share"] = (weekly_olist(c, "avg(case when k > 1 then 1.0 else 0 end)", frm=rep), "original", OLIST, "olist", ())
    arms = {r_[0]: r_[2] for r_ in card("L4-ads")["estimate"]["criteo_arms"]}
    r = R("ad_exposure")
    exp = [(datetime(2018, 1, 8) + timedelta(minutes=int(m)), {"session_id": f"s{k}", "campaign_id": "criteo-like-1", "treated": bool(t)})
           for k, (m, t) in enumerate(zip(sorted(r.integers(0, 600, 30)), r.random(30) < 0.85))]
    B["ad_exposure"] = (events([e for e in exp if e[1]["treated"] or True], "ad_exposure"), "synthetic_generated", cs, "stitch-card:L4-ads",
                        ("mech:criteo_assignment_and_one_sided_exposure",))
    hill = q(c, f"select replace(segment, ' ', '_'), conversion, visit from read_csv('{RAW}/hillstrom/hillstrom.csv') using sample 300 rows (reservoir, {seed})")
    B["experiment_conversion"] = (AssignmentOutcomes(shape="assignment_outcomes", study_id="hillstrom-email-2008", arms=tuple(sorted({h[0] for h in hill})),
                                                     units=tuple(AssignedUnit(unit_id=f"u{i}", arm=h[0], outcome=float(h[1])) for i, h in enumerate(hill))),
                                  "external_study", EXTERNAL("hillstrom"), "hillstrom", ("mech:randomised_email_campaign",))

    # ---------------- L5
    pt = q(c, f"select payment_type from read_csv('{O}/olist_order_payments_dataset.csv') using sample 40 rows (reservoir, {seed})")
    B["payment_type"] = ([CategoricalValue(shape="categorical", category=p[0]) for p in pt], "original", OLIST, "olist", ())
    o1 = q(c, f"""select order_purchase_timestamp, order_approved_at, order_delivered_carrier_date, order_delivered_customer_date from {ORD}
                  where order_status='delivered' and order_delivered_carrier_date > order_approved_at order by hash(order_id || '{seed}') limit 1""")[0]
    B["order_status"] = (traj(list(zip(o1, ("created", "approved", "shipped", "delivered")))), "original", OLIST, "olist",
                         ("mech:status_from_lifecycle_timestamps",))
    B["cancellations_count"] = (weekly_olist(c, "count(*) filter (where order_status in ('canceled','unavailable'))"), "original", OLIST, "olist", ())
    rs = float(np.mean(card("L5-returns")["estimate"]["uci_return_line_share"]))
    dl = from_port(emu_port, "deliveries_count", "metric")
    if isinstance(dl, tuple):
        B["returns_count"] = (ts([(p.t, round(p.value * rs)) for p in dl[0].payload.points]), "synthetic_generated", cs, "stitch-card:L5-returns",
                              ("mech:uci_return_share_transferred",))

    # ---------------- L6
    sent = "review_creation_date"
    B["surveys_sent_count"] = (weekly_olist(c, "count(*)", time_col=sent, frm=REV), "original", OLIST, "olist", ())
    B["surveys_answered_count"] = (weekly_olist(c, "count(*) filter (where review_answer_timestamp is not null)", time_col=sent, frm=REV),
                                   "original", OLIST, "olist", ())
    B["survey_response_share"] = (weekly_olist(c, "avg(case when review_answer_timestamp is not null then 1.0 else 0 end)", time_col=sent, frm=REV),
                                  "original", OLIST, "olist", ())
    B["review_answer_lag_p50_hours"] = (weekly_olist(c, "median(epoch(review_answer_timestamp - review_creation_date)/3600)", time_col=sent, frm=REV),
                                        "original", OLIST, "olist", ())
    tx = q(c, f"""select review_answer_timestamp, order_id, review_score, review_comment_message from {REV}
                  where review_comment_message is not null order by hash(review_id || '{seed}') limit 20""")
    B["review_text_submitted"] = (events([(r_[0], {"order_id": r_[1], "score": int(r_[2]), "language": "pt-BR", "text": r_[3][:500]}) for r_ in tx],
                                         "review_text_submitted"), "original", OLIST, "olist", ("mech:customer_free_text",))
    fr = card("L6-fraud")["estimate"]["yelpchi"]["fraud_share"]
    ans = weekly_olist(c, "count(*) filter (where review_answer_timestamp is not null)", time_col=sent, frm=REV)
    B["flagged_reviews_count"] = (ts([(p.t, round(p.value * fr * 0.1)) for p in ans.points]), "synthetic_generated", cs, "stitch-card:L6-fraud",
                                  ("mech:yelpchi_spam_share_x_assumed_detector_recall_0.1",))

    # ---------------- L7, L9
    B["ranking_policy_version"] = (traj([(datetime(2018, 1, 1), "v1"), (datetime(2018, 2, 26), "v2")]), "synthetic_generated", cs,
                                   "emulator-release-log", ("mech:assumed_release_schedule",))
    B["deployment"] = (events([(datetime(2018, 2, 20, 6), {"service": "checkout", "version": "3.1.0"}),
                               (datetime(2018, 2, 20, 6, 5), {"service": "payment", "version": "3.1.0"})], "deployment"),
                       "synthetic_generated", cs, "emulator-release-log", ("mech:astronomy_shop_pinned_release",))
    B["event_schema_version"] = (traj([(datetime(2018, 1, 1), "v1")]), "synthetic_generated", cs, "assumption", ("mech:assumed_schema_version",))
    B["out_of_order_records_count"] = (weekly_olist(c, "count(*) filter (where order_delivered_carrier_date < order_approved_at)"),
                                       "original", OLIST, "olist", ("mech:handover_before_approval",))
    ev = emu_port._e.events
    import polars as pl
    lag = (ev.filter((pl.col("event_time") >= datetime(2018, 1, 1)) & (pl.col("event_time") < datetime(2018, 3, 5)))
           .with_columns(pl.col("event_time").dt.truncate("1w").alias("w"), ((pl.col("available_at") - pl.col("event_time")).dt.total_seconds()).alias("lag"))
           .group_by("w").agg(pl.col("lag").quantile(0.95).alias("p95"), (pl.col("revision") > 0).sum().alias("rev")).sort("w"))
    B["ingestion_lag_p95_s"] = (ts([(r_["w"], r_["p95"]) for r_ in lag.to_dicts()]), "synthetic_generated", cs, "observation-model", ("mech:assumed_A-11",))
    B["late_corrections_count"] = (ts([(r_["w"], r_["rev"]) for r_ in lag.to_dicts()]), "synthetic_generated", cs, "observation-model", ("mech:assumed_A-12",))
    rr = f"read_csv('{RAW}/retailrocket/events.csv')"
    pw = q(c, f"""with b as (select visitorid, itemid, min(timestamp) t from {rr} where event='transaction' group by 1, 2),
                   v as (select visitorid, itemid, min(timestamp) t from {rr} where event='view' group by 1, 2)
                   select date_trunc('week', to_timestamp(b.t/1000)) w, count(*) filter (where v.t is null or v.t > b.t), count(*)
                   from b left join v using(visitorid, itemid) group by 1 order by 1""")
    ext = EXTERNAL("retailrocket")
    B["purchases_without_view_count"] = (ts([(r_[0], r_[1]) for r_ in pw]), "external_study", ext, "retailrocket", ("mech:transfer_evidence",))
    B["purchases_without_view_share"] = (ts([(r_[0], r_[1] / r_[2]) for r_ in pw]), "external_study", ext, "retailrocket", ("mech:transfer_evidence",))
    return B


def _canon():
    from .. import pipeline
    from ..config import Config
    return pipeline.canonical_olist(Config.load())
