"""Olist adapter: raw CSVs -> canonical tables + canonical observed events.

This is the only module that knows Olist column names. Raw IDs are namespaced ("olist:")
so they can never collide with generated or other-source IDs.

Timestamps are Olist's naive local times (America/Sao_Paulo); kept naive, documented in
the manifest. Known data quirks (docs §4.1b) are kept and flagged, not silently dropped.
"""
from __future__ import annotations

import pathlib

import duckdb
import polars as pl

from .. import SCHEMA_VERSION
from ..contracts import EVENT_STREAMS, conform
from ..registry import SourceManifest

NS = "olist"


def _con(src: pathlib.Path) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    for t in ["orders", "order_items", "order_payments", "order_reviews", "customers", "sellers", "products"]:
        c.execute(f"create view raw_{t} as select * from read_csv_auto('{src}/olist_{t}_dataset.csv')")
    c.execute(f"create view raw_cat as select * from read_csv_auto('{src}/product_category_name_translation.csv')")
    return c


def build(manifest: SourceManifest, out: pathlib.Path) -> dict:
    """Write canonical tables and events to `out`; return summary stats."""
    c = _con(pathlib.Path(manifest.path))
    out.mkdir(parents=True, exist_ok=True)
    q = {
        "orders": """select 'olist:'||o.order_id order_id, 'olist:'||c.customer_unique_id buyer_id,
                c.customer_state buyer_region, o.order_status status,
                o.order_purchase_timestamp purchased_at, o.order_approved_at approved_at,
                o.order_delivered_carrier_date shipped_at, o.order_delivered_customer_date delivered_at,
                o.order_estimated_delivery_date promised_at
            from raw_orders o join raw_customers c using(customer_id)""",
        "order_items": """select 'olist:'||order_id order_id, order_item_id item_no, 'olist:'||product_id product_id,
                'olist:'||seller_id seller_id, price, freight_value freight, shipping_limit_date
            from raw_order_items""",
        "payments": """select 'olist:'||order_id order_id, payment_sequential seq, payment_type,
                payment_installments installments, payment_value amount from raw_order_payments""",
        # dedupe: Olist has review rows shared across orders and >1 review per order (§4.1b);
        # keep one row per (review_id, order_id), flag multi-review orders
        "reviews": """select 'olist:'||review_id review_id, 'olist:'||order_id order_id, review_score score,
                review_comment_message is not null has_text, review_creation_date created_at,
                review_answer_timestamp answered_at,
                count(*) over (partition by order_id) > 1 as multi_review_order
            from (select distinct on (review_id, order_id) * from raw_order_reviews)""",
        "sellers": "select 'olist:'||seller_id seller_id, seller_state region, seller_city city from raw_sellers",
        "products": """select 'olist:'||p.product_id product_id,
                coalesce(t.product_category_name_english, p.product_category_name, 'unknown') category,
                p.product_weight_g weight_g from raw_products p left join raw_cat t using(product_category_name)""",
    }
    stats = {}
    for name, sql in q.items():
        c.execute(f"copy ({sql}) to '{out}/{name}.parquet' (format parquet)")
        stats[name] = c.execute(f"select count(*) from '{out}/{name}.parquet'").fetchone()[0]
    stats["events"] = build_events(out)
    return stats


def build_events(out: pathlib.Path) -> int:
    """Canonical observed events for the real-only replay track.

    Semantics follow docs/p0/PHASE1_SOURCE_SEMANTICS.md (gate S1):
      order_created      purchase time; order-level amount; NO seller/product (orders can span sellers)
      order_item         one per item at purchase time: seller, product, amount = price, value = freight
      payment_approved   approval time; amount = sum of payment records (Olist has no settlement data)
      handed_to_carrier  seller's handover to the logistics partner (can precede approval: data quirk)
      delivered          customer delivery; value = days late vs the promise
      survey_sent        Olist emails the satisfaction survey (date only)
      review_submitted   the CUSTOMER answers the survey; value = score
      order_status_snapshot  final status at the data cut (cancellation has no timestamp in the source)
    """
    c = duckdb.connect()
    for t in ["orders", "order_items", "payments", "reviews", "sellers", "products"]:
        c.execute(f"create view {t} as select * from '{out}/{t}.parquet'")
    cut = c.execute("""select greatest(max(purchased_at), max(approved_at), max(shipped_at), max(delivered_at)) from orders""").fetchone()[0]
    c.execute("""create view o2 as select o.*, date_diff('day', o.purchased_at, o.promised_at) promised_days,
             (select sum(price + freight) from order_items x where x.order_id = o.order_id) items_amount,
             (select sum(amount) from payments p where p.order_id = o.order_id) paid_amount from orders o""")
    c.execute("""create view items_ev as select i.*, p.category, o.buyer_id, o.buyer_region, o.purchased_at
             from order_items i join orders o using(order_id) left join products p using(product_id)""")
    o_cols = "buyer_id, order_id, null::varchar seller_id, null::varchar product_id, buyer_region as region, null::varchar category"
    parts = [
        f"select 'order_created' event_type, purchased_at event_time, {o_cols}, items_amount amount, promised_days::double value, null::varchar metric from o2",
        """select 'order_item', purchased_at, buyer_id, order_id, seller_id, product_id, buyer_region, category, price, freight, null from items_ev""",
        f"select 'payment_approved', approved_at, {o_cols}, paid_amount, null, null from o2 where approved_at is not null",
        f"select 'handed_to_carrier', shipped_at, {o_cols}, null, null, null from o2 where shipped_at is not null",
        f"""select 'delivered', delivered_at, {o_cols}, null, date_diff('day', promised_at, delivered_at)::double, null
            from o2 where delivered_at is not null""",
        f"""select 'survey_sent', r.created_at, {o_cols.replace('buyer_id', 'h.buyer_id').replace('order_id', 'h.order_id')}, null, null, null
            from reviews r join o2 h using(order_id)""",
        f"""select 'review_submitted', r.answered_at, {o_cols.replace('buyer_id', 'h.buyer_id').replace('order_id', 'h.order_id')}, null, r.score::double, null
            from reviews r join o2 h using(order_id) where r.answered_at is not null""",
        f"select 'order_status_snapshot', timestamp '{cut}', {o_cols}, null, null, status from o2",
    ]
    df = c.sql(" union all ".join(parts)).pl()
    df = df.with_columns(
        pl.concat_str([pl.lit("olist:ev:"), pl.col("event_type"), pl.lit(":"), pl.col("order_id").str.slice(6),
                       pl.lit(":"), pl.int_range(pl.len()).cast(pl.Utf8)]).alias("event_id"),
        pl.col("event_type").replace_strict(EVENT_STREAMS, default="web").alias("stream"),
        pl.lit("BRL").alias("currency"),
        pl.lit("observed").alias("prov_class"),
        pl.concat_str([pl.lit(f"prov:{NS}:s{SCHEMA_VERSION}:"), pl.col("event_type")]).alias("prov_id"),
    )
    ev = conform(df, with_available=False).sort("event_time")
    ev.write_parquet(out / "events.parquet")
    return ev.height
