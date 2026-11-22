"""Retailrocket adapter: only aggregate funnel statistics are exported (statistical transfer,
docs §3.2). No Retailrocket entity ever enters a generated world."""
from __future__ import annotations

import pathlib

import duckdb

from ..registry import SourceManifest


def funnel_stats(manifest: SourceManifest) -> dict:
    """Session-level funnel rates. A 'session' is a visitor-day (Retailrocket has no session id:
    this is an explicit approximation, assumption A-02)."""
    src = pathlib.Path(manifest.path) / "events.csv"
    c = duckdb.connect()
    c.execute(f"""create view s as select visitorid, cast(to_timestamp(timestamp/1000) as date) d,
        sum((event='view')::int) views, sum((event='addtocart')::int) carts, sum((event='transaction')::int) txns
        from read_csv_auto('{src}') group by 1,2""")
    r = c.execute("""select count(*) sessions, avg(views) views_mean,
        avg((carts>0)::int) p_cart, sum(((carts>0) and (txns>0))::int)/sum((carts>0)::int) p_buy_given_cart,
        sum(((carts=0) and (txns>0))::int)/sum((carts=0)::int) p_buy_direct,
        avg((txns>0)::int) p_buy from s""").fetchone()
    keys = ["sessions", "views_mean", "p_cart", "p_buy_given_cart", "p_buy_direct", "p_buy"]
    return {k: float(v) for k, v in zip(keys, r)}
