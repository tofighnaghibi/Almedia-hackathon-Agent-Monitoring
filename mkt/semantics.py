"""Source semantics for Olist: field dictionary + gate S1 (semantic validity) and S2 (join & lineage).

Phase 1 of the approved plan (ARCHITECTURE.md §13.0, gates §12.5). Every field gets a documented
meaning, unit, granularity, the actor who produces it, and where that meaning comes from:
  docs      Olist's own dataset description (Kaggle, olistbr/brazilian-ecommerce)
  measured  established by a check in this module
  inferred  our interpretation; must be confirmed by a check or stays an open question
A check result is PASS, EXCEPTION (real data quirk, documented and kept) or FAIL (blocks the gate).
"""
from __future__ import annotations

import pathlib
from dataclasses import asdict, dataclass

import duckdb


@dataclass(frozen=True)
class Field:
    table: str
    name: str
    meaning: str
    kind: str          # identifier | event_time | date_only | measure | category | label | text | location
    unit: str
    actor: str         # customer | seller | platform | carrier | payment | olist-anonymisation
    source: str        # docs | measured | inferred
    note: str = ""


F = Field
FIELDS: list[Field] = [
    # orders
    F("orders", "order_id", "order identifier", "identifier", "-", "platform", "docs"),
    F("orders", "customer_id", "customer key PER ORDER (not a person): each order gets its own customer_id", "identifier", "-", "platform", "docs",
      "use customer_unique_id for the person; checked: 1 customer_id per order"),
    F("orders", "order_status", "lifecycle status at the data cut", "category", "-", "platform", "docs",
      "values incl. delivered, shipped, canceled, unavailable, invoiced, processing, created, approved; 'unavailable' meaning inferred: no item could be fulfilled"),
    F("orders", "order_purchase_timestamp", "when the customer placed the order", "event_time", "local time", "customer", "docs"),
    F("orders", "order_approved_at", "when the payment was approved", "event_time", "local time", "payment", "docs"),
    F("orders", "order_delivered_carrier_date", "when the SELLER handed the order to the logistics partner", "event_time", "local time", "seller", "docs",
      "not 'shipped by platform'; can precede approval (data quirk)"),
    F("orders", "order_delivered_customer_date", "when the carrier delivered to the customer", "event_time", "local time", "carrier", "docs"),
    F("orders", "order_estimated_delivery_date", "delivery date promised to the customer at purchase", "date_only", "date", "platform", "docs",
      "date only (00:00:00); the promise, not an event"),
    # items
    F("order_items", "order_item_id", "sequence number of the item WITHIN the order (1..n)", "identifier", "-", "platform", "docs",
      "there is no quantity column: quantity = number of rows per (order, product)"),
    F("order_items", "product_id", "product identifier", "identifier", "-", "platform", "docs"),
    F("order_items", "seller_id", "seller fulfilling THIS item (items of one order can have different sellers)", "identifier", "-", "seller", "docs"),
    F("order_items", "shipping_limit_date", "deadline for the seller to hand the item to the logistics partner", "event_time", "local time", "platform", "docs"),
    F("order_items", "price", "item price", "measure", "BRL", "seller", "docs"),
    F("order_items", "freight_value", "freight charged for the item (order freight split over items)", "measure", "BRL", "platform", "docs"),
    # payments
    F("order_payments", "payment_sequential", "sequence when one order is paid with several methods", "identifier", "-", "payment", "docs"),
    F("order_payments", "payment_type", "method: credit_card, boleto, voucher, debit_card, not_defined", "category", "-", "customer", "docs"),
    F("order_payments", "payment_installments", "number of instalments chosen", "measure", "count", "customer", "docs"),
    F("order_payments", "payment_value", "amount paid with this method", "measure", "BRL", "payment", "docs",
      "a PAYMENT record; Olist has no seller settlement/payout data"),
    # reviews
    F("order_reviews", "review_id", "review (survey) identifier", "identifier", "-", "platform", "docs", "NOT unique: some review_ids cover several orders"),
    F("order_reviews", "review_score", "1-5 score the CUSTOMER gave in the satisfaction survey", "label", "1-5", "customer", "docs"),
    F("order_reviews", "review_comment_title", "optional survey comment title", "text", "pt-BR", "customer", "docs"),
    F("order_reviews", "review_comment_message", "optional survey comment", "text", "pt-BR", "customer", "docs",
      "store/partner names replaced by Game of Thrones house names (anonymisation)"),
    F("order_reviews", "review_creation_date", "date Olist SENT the satisfaction survey (at delivery or at the estimated date)", "date_only", "date", "platform", "docs",
      "NOT when the customer reviewed"),
    F("order_reviews", "review_answer_timestamp", "when the CUSTOMER answered the survey", "event_time", "local time", "customer", "docs",
      "NOT a seller reply (corrected 21 Nov); this is when the score becomes known"),
    # customers / sellers / products / geolocation
    F("customers", "customer_unique_id", "the person (stable across orders)", "identifier", "-", "customer", "docs"),
    F("customers", "customer_zip_code_prefix", "first 5 digits of the customer's ZIP, as quoted text with leading zeros", "location", "zip5", "customer", "measured"),
    F("customers", "customer_city", "customer city", "location", "-", "customer", "docs"),
    F("customers", "customer_state", "customer state (UF)", "location", "-", "customer", "docs"),
    F("sellers", "seller_id", "seller identifier", "identifier", "-", "seller", "docs"),
    F("sellers", "seller_zip_code_prefix", "first 5 digits of the seller's ZIP", "location", "zip5", "seller", "docs"),
    F("sellers", "seller_city", "seller city", "location", "-", "seller", "docs"),
    F("sellers", "seller_state", "seller state (UF)", "location", "-", "seller", "docs"),
    F("products", "product_category_name", "category name in Portuguese", "category", "pt-BR", "seller", "docs", "English via the translation table"),
    F("products", "product_name_lenght", "NUMBER OF CHARACTERS in the product name (the name itself is not released)", "measure", "characters", "seller", "docs",
      "spelling 'lenght' is in the source"),
    F("products", "product_description_lenght", "number of characters in the description", "measure", "characters", "seller", "docs"),
    F("products", "product_photos_qty", "number of published photos", "measure", "count", "seller", "docs"),
    F("products", "product_weight_g", "weight", "measure", "g", "seller", "docs"),
    F("products", "product_length_cm", "length", "measure", "cm", "seller", "docs"),
    F("products", "product_height_cm", "height", "measure", "cm", "seller", "docs"),
    F("products", "product_width_cm", "width", "measure", "cm", "seller", "docs"),
    F("geolocation", "geolocation_zip_code_prefix", "ZIP prefix", "location", "zip5", "platform", "docs",
      "NOT a key: many lat/lng rows per prefix"),
    F("geolocation", "geolocation_lat", "latitude of a point in the prefix", "location", "degrees", "platform", "docs"),
    F("geolocation", "geolocation_lng", "longitude of a point in the prefix", "location", "degrees", "platform", "docs"),
    # cross-cutting assumption
    F("*", "(all timestamps)", "local time, naive (no timezone)", "event_time", "local time", "platform", "inferred",
      "assumed America/Sao_Paulo; Brazil spans several zones and had DST in 2016-2018: hour-level analyses carry this uncertainty"),
]

# Counts measured on 21 Nov 2026 (ARCHITECTURE.md §4.1b): phase 1 must reproduce them exactly.
EXPECTED_COUNTS = {"orders": 99441, "order_items": 112650, "order_payments": 103886, "order_reviews": 99224,
                   "customers": 99441, "sellers": 3095, "products": 32951}
TABLES = ["orders", "order_items", "order_payments", "order_reviews", "customers", "sellers", "products", "geolocation"]


@dataclass
class Check:
    gate: str
    id: str
    fields: str
    expectation: str
    measured: str
    status: str        # PASS | EXCEPTION | FAIL
    action: str = ""


def _con(src: pathlib.Path) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    for t in TABLES:
        c.execute(f"create view {t} as select * from read_csv_auto('{src}/olist_{t}_dataset.csv')")
    c.execute(f"create view cat as select * from read_csv_auto('{src}/product_category_name_translation.csv')")
    return c


def _one(c, sql):
    return c.execute(sql).fetchone()


def audit(src: pathlib.Path) -> dict:
    c = _con(pathlib.Path(src))
    out: list[Check] = []

    def add(gate, id_, fields, expectation, measured, ok, exception=False, action=""):
        out.append(Check(gate, id_, fields, expectation, measured, "PASS" if ok else ("EXCEPTION" if exception else "FAIL"), action))

    # ---------------- S2: keys, joins, multiplicity, lineage ----------------
    for t, n in EXPECTED_COUNTS.items():
        got = _one(c, f"select count(*) from {t}")[0]
        add("S2", f"count.{t}", t, f"{n:,} rows (measured 21 Nov)", f"{got:,}", got == n)
    for t, key in [("orders", "order_id"), ("customers", "customer_id"), ("sellers", "seller_id"), ("products", "product_id")]:
        dup = _one(c, f"select count(*) - count(distinct {key}) from {t}")[0]
        add("S2", f"pk.{t}", f"{t}.{key}", "unique", f"{dup} duplicates", dup == 0)
    dup = _one(c, "select count(*) - count(distinct (order_id, order_item_id)) from order_items")[0]
    add("S2", "pk.order_items", "order_items.(order_id, order_item_id)", "unique", f"{dup} duplicates", dup == 0)
    dup = _one(c, "select count(*) - count(distinct (order_id, payment_sequential)) from order_payments")[0]
    add("S2", "pk.order_payments", "order_payments.(order_id, payment_sequential)", "unique", f"{dup} duplicates", dup == 0)
    multi_rev, shared_rev, dup_pair = _one(c, """select
        (select count(*) from (select order_id from order_reviews group by 1 having count(*) > 1)),
        (select count(*) from (select review_id from order_reviews group by 1 having count(distinct order_id) > 1)),
        (select count(*) - count(distinct (review_id, order_id)) from order_reviews)""")
    add("S2", "pk.order_reviews", "order_reviews.review_id", "review_id unique",
        f"{shared_rev} review_ids cover >1 order; {multi_rev} orders have >1 review; {dup_pair} exact duplicate pairs",
        False, exception=True, action="key = (review_id, order_id); keep the latest answer per order for 'the' score; flag multi-review orders")
    for child, parent, key in [("order_items", "orders", "order_id"), ("order_payments", "orders", "order_id"),
                               ("order_reviews", "orders", "order_id"), ("orders", "customers", "customer_id"),
                               ("order_items", "sellers", "seller_id"), ("order_items", "products", "product_id")]:
        orph = _one(c, f"select count(*) from {child} anti join {parent} using({key})")[0]
        add("S2", f"fk.{child}->{parent}", f"{child}.{key}", "every row has a parent", f"{orph} orphans", orph == 0)
    no_items = c.execute("select o.order_status, count(*) from orders o anti join order_items i using(order_id) group by 1 order by 2 desc").fetchall()
    add("S2", "mult.orders->items", "orders 1..n items", "every order has >=1 item",
        f"{sum(n for _, n in no_items)} orders without items: {no_items}", False, exception=True,
        action="legitimate: unavailable/canceled orders carry no items; never impute items")
    no_pay = _one(c, "select count(*) from orders anti join order_payments using(order_id)")[0]
    add("S2", "mult.orders->payments", "orders 1..n payments", "every order has >=1 payment", f"{no_pay} without payment",
        no_pay == 0, exception=no_pay <= 5, action="keep; mark payment unknown")
    cust_per_order = _one(c, "select max(k) from (select customer_id, count(*) k from orders group by 1)")[0]
    add("S2", "mult.customer_id", "orders.customer_id", "exactly one order per customer_id (doc: per-order key)",
        f"max orders per customer_id = {cust_per_order}", cust_per_order == 1)
    geo = _one(c, "select count(*), count(distinct geolocation_zip_code_prefix) from geolocation")
    add("S2", "geo.not_a_key", "geolocation.zip_prefix", "many points per prefix (not a join key)",
        f"{geo[0]:,} rows for {geo[1]:,} prefixes", geo[0] > geo[1],
        action="join via aggregated prefix centroid only; never row-join (would multiply rows)")
    gap = _one(c, """select count(*) from (select order_id, max(order_item_id) m, count(*) n from order_items group by 1) where m <> n""")[0]
    add("S2", "items.sequence", "order_items.order_item_id", "1..n without gaps per order", f"{gap} orders with gaps", gap == 0)

    # ---------------- S1: meaning, units, granularity ----------------
    t_only = _one(c, """select avg((strftime(order_estimated_delivery_date, '%H:%M:%S') = '00:00:00')::int),
                        (select avg((strftime(review_creation_date, '%H:%M:%S') = '00:00:00')::int) from order_reviews),
                        avg((strftime(order_purchase_timestamp, '%H:%M:%S') = '00:00:00')::int) from orders""")
    add("S1", "grain.estimated_delivery", "order_estimated_delivery_date", "date only", f"{t_only[0]:.1%} at 00:00:00", t_only[0] > 0.999)
    add("S1", "grain.review_creation", "review_creation_date", "date only (survey-sent date)", f"{t_only[1]:.1%} at 00:00:00", t_only[1] > 0.999)
    add("S1", "grain.purchase", "order_purchase_timestamp", "full timestamp", f"{t_only[2]:.2%} at exactly midnight", t_only[2] < 0.01)
    o = _one(c, """select count(*),
        sum((order_approved_at < order_purchase_timestamp)::int),
        sum((order_delivered_carrier_date < order_approved_at)::int),
        sum((order_delivered_customer_date < order_delivered_carrier_date)::int),
        sum((order_estimated_delivery_date < cast(order_purchase_timestamp as date))::int) from orders""")
    add("S1", "order.purchase<=approved", "purchase, approved", "payment approved after purchase", f"{o[1]} violations", o[1] == 0)
    add("S1", "order.approved<=carrier", "approved, delivered_carrier", "handover after approval", f"{o[2]:,} handovers before approval",
        False, exception=True, action="keep, flag; event order not guaranteed: replay must not assume approval precedes handover")
    add("S1", "order.carrier<=delivered", "delivered_carrier, delivered_customer", "delivery after handover", f"{o[3]} deliveries before handover",
        False, exception=True, action="keep, flag as timestamp error")
    add("S1", "order.promise>=purchase", "estimated_delivery", "promise not before purchase", f"{o[4]} violations", o[4] == 0)
    st = _one(c, """select sum((order_status = 'delivered' and order_delivered_customer_date is null)::int),
                    sum((order_status <> 'delivered' and order_delivered_customer_date is not null)::int),
                    sum((order_status = 'canceled' and order_delivered_customer_date is not null)::int) from orders""")
    add("S1", "status.delivered<=>date", "order_status, delivered_customer_date", "status 'delivered' iff a delivery date",
        f"{st[0]} delivered without date; {st[1]} with date but other status ({st[2]} canceled after delivery)", False, exception=True,
        action="status is a snapshot at the data cut; trust timestamps for events, status for final state")
    canc_time = "no cancellation timestamp exists in any table"
    add("S1", "status.cancel_time", "order_status=canceled/unavailable", "a time for cancellation", canc_time, False, exception=True,
        action="cancellation is a STATE with unknown time; never stamp it with the purchase time (adapter fixed)")
    lim = _one(c, """select avg((o.order_delivered_carrier_date > i.shipping_limit_date)::int) from order_items i join orders o using(order_id)
                     where o.order_delivered_carrier_date is not null""")[0]
    add("S1", "items.shipping_limit", "shipping_limit_date vs delivered_carrier", "seller deadline before most handovers",
        f"{lim:.1%} of items handed over after the deadline", lim < 0.3, action="seller lateness signal (real)")
    rv = _one(c, """select count(*), avg((r.review_answer_timestamp >= r.review_creation_date)::int),
        avg((abs(date_diff('day', cast(o.order_delivered_customer_date as date), r.review_creation_date)) <= 1
             or abs(date_diff('day', o.order_estimated_delivery_date, r.review_creation_date)) <= 1)::int),
        avg((r.review_creation_date < cast(o.order_delivered_customer_date as date))::int)
        from order_reviews r join orders o using(order_id) where o.order_delivered_customer_date is not null""")
    add("S1", "review.answer>=sent", "review_answer_timestamp, review_creation_date", "customer answers after the survey is sent",
        f"{rv[1]:.2%} of {rv[0]:,}", rv[1] > 0.999)
    add("S1", "review.sent_at_delivery_or_estimate", "review_creation_date", "survey sent ~ at delivery or at the estimated date (Olist docs)",
        f"{rv[2]:.1%} within ±1 day of delivery or estimate; {rv[3]:.1%} sent before delivery", rv[2] > 0.9,
        action="confirms docs: creation = survey sent; review event time = answer time")
    got = _one(c, """select count(*) from order_reviews where regexp_matches(lower(coalesce(review_comment_message, '')),
                     '(stark|lannister|targaryen|baratheon|greyjoy|tyrell|martell|tully|arryn)')""")[0]
    add("S1", "review.anonymisation", "review_comment_message", "store names replaced by Game of Thrones houses (docs)",
        f"{got} comments mention a GoT house", got > 0, action="treat house names as a placeholder token, not real entities")
    money = _one(c, """select (select sum((price <= 0)::int) + sum((freight_value < 0)::int) from order_items),
                       (select sum((payment_value < 0)::int) from order_payments),
                       (select avg((abs(d) <= 1)::int) from (select i.order_id, sum(price + freight_value) - p.v d from order_items i
                         join (select order_id, sum(payment_value) v from order_payments group by 1) p using(order_id) group by i.order_id, p.v))""")
    add("S1", "money.signs", "price, freight_value, payment_value", "price > 0, freight >= 0, payment >= 0 (BRL)", f"{money[0] + money[1]} violations",
        money[0] + money[1] == 0)
    add("S1", "money.reconcile", "items vs payments", "payments = price + freight per order (BRL)", f"{money[2]:.2%} within 1 BRL", money[2] > 0.99,
        exception=True, action="residual = instalment interest / vouchers; no settlement data exists, so no 'settled' event")
    inst = c.execute("select payment_type, min(payment_installments), max(payment_installments) from order_payments group by 1 order by 1").fetchall()
    bad_inst = _one(c, "select count(*) from order_payments where payment_installments < 1")[0]
    add("S1", "payments.installments", "payment_installments", ">= 1; > 1 only for credit cards", f"{inst}; {bad_inst} rows with 0",
        bad_inst == 0, exception=bad_inst < 10, action="0 instalments = data error; treat as 1")
    lens = _one(c, "select any_value(typeof(product_name_lenght)), max(product_name_lenght), count(product_name_lenght) from products")
    add("S1", "products.name_is_length", "product_name_lenght", "a NUMBER of characters, not a name", f"type {lens[0]}, max {lens[1]}",
        lens[0] in ("BIGINT", "DOUBLE", "INTEGER"), action="titles must come from the ESCI bridge (labelled)")
    zips = _one(c, """select any_value(typeof(customer_zip_code_prefix)), min(length(customer_zip_code_prefix::varchar)),
                      max(length(customer_zip_code_prefix::varchar)),
                      (select min(length(geolocation_zip_code_prefix::varchar)) from geolocation) from customers""")
    add("S1", "zip.prefix5", "customer/seller/geolocation zip prefix", "5-character text with leading zeros kept",
        f"type {zips[0]}, length {zips[1]}..{zips[2]} (geolocation min {zips[3]})", zips[0] == "VARCHAR" and zips[1] == zips[2] == 5,
        action="always read as text; never cast to integer (would drop leading zeros, e.g. '09790')")
    tr = _one(c, """select avg((t.product_category_name_english is not null)::int) from products p left join cat t using(product_category_name)
                    where p.product_category_name is not null""")[0]
    untrans = [r[0] for r in c.execute("""select distinct p.product_category_name from products p anti join cat t using(product_category_name)
                                         where p.product_category_name is not null""").fetchall()]
    add("S1", "category.translation", "product_category_name", "every category has an English name", f"{tr:.2%}; missing: {untrans}",
        tr == 1, exception=True, action="keep Portuguese name for the untranslated ones")
    s1 = [x for x in out if x.gate == "S1"]
    s2 = [x for x in out if x.gate == "S2"]
    return {"fields": [asdict(f) for f in FIELDS], "checks": [asdict(x) for x in out],
            "gates": {g: ("PASS" if not any(x.status == "FAIL" for x in xs) else "FAIL") for g, xs in (("S1", s1), ("S2", s2))}}


def report_md(res: dict) -> str:
    L = ["# Phase 1: Olist source semantics (gates S1, S2)", "",
         f"**Gate S1 (semantic validity): {res['gates']['S1']}** · **Gate S2 (join & lineage): {res['gates']['S2']}**", "",
         "PASS = as expected · EXCEPTION = real data quirk, documented and handled · FAIL = blocks the gate.", "",
         "## Checks", "", "| Gate | Check | Field(s) | Expectation | Measured | Status | Handling |", "|---|---|---|---|---|---|---|"]
    for x in res["checks"]:
        L.append(f"| {x['gate']} | {x['id']} | {x['fields']} | {x['expectation']} | {str(x['measured']).replace('|', '/')} | **{x['status']}** | {x['action']} |")
    L += ["", "## Field dictionary", "", "| Table | Field | Meaning | Kind | Unit | Actor | Source of meaning | Note |", "|---|---|---|---|---|---|---|---|"]
    for f in res["fields"]:
        L.append(f"| {f['table']} | `{f['name']}` | {f['meaning']} | {f['kind']} | {f['unit']} | {f['actor']} | {f['source']} | {f['note']} |")
    return "\n".join(L) + "\n"
