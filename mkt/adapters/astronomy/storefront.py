"""M4 storefront projection + M5 flag adapter for Astronomy Shop 3.1.0 (upstream untouched).

catalog_sync   top-N Olist products by 2017 demand -> catalog.products UPSERT (USD, public 10-char ids)
SessionDriver  plays emulator session plans over HTTP: product -> cart -> checkout; idempotent by command_id
FlagAdapter    operational faults -> flag file (atomic write) + read-back through the flag service

The driver never writes business state: it returns CommandResults; the emulator commits or releases.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request

import numpy as np
import polars as pl

from ...sim.chain import uniform

BASE = "http://localhost:8080"
FX_SOURCE = "BCB SGS usd_brl, mean of 2017 daily closes (trained_through 2017-12-31)"
PICTURES = ("Telescope.jpg",)        # any image the image-provider serves; products are not shown visually


def public_id(product_id: str) -> str:
    return base64.b32encode(hashlib.sha256(product_id.encode()).digest()).decode()[:10]


def fx_2017(raw_dir: pathlib.Path) -> float:
    v = json.loads((raw_dir / "bcb/usd_brl.json").read_text())
    xs = [float(r["valor"]) for r in v if r["data"].endswith("2017")]
    return sum(xs) / len(xs)


def build_catalog(canonical_dir: pathlib.Path, raw_dir: pathlib.Path, n: int = 200) -> pl.DataFrame:
    """Top-n products by 2017 order count (fitted on 2017 only), mean 2017 price in USD."""
    items = pl.read_parquet(canonical_dir / "order_items.parquet")
    orders = pl.read_parquet(canonical_dir / "orders.parquet").select("order_id", "purchased_at")
    prods = pl.read_parquet(canonical_dir / "products.parquet")
    x = (items.join(orders, on="order_id").filter(pl.col("purchased_at").dt.year() == 2017)
         .group_by("product_id").agg(pl.len().alias("orders"), pl.col("price").mean().alias("price_brl"))
         .sort(["orders", "product_id"], descending=[True, False]).head(n)
         .join(prods, on="product_id", how="left"))                 # canonical categories are already English
    rate = fx_2017(raw_dir)
    return x.with_columns(
        pl.col("product_id").map_elements(public_id, return_dtype=pl.Utf8).alias("public_id"),
        (pl.col("price_brl") / rate).alias("price_usd"),
        pl.col("category").fill_null("other").alias("category_en"),
        (pl.col("orders") / pl.col("orders").sum()).alias("weight"))


def catalog_sync(cat: pl.DataFrame, container: str = "astronomy-db") -> int:
    """UPSERT the projection into catalog.products (demo rows are left alone)."""
    rows = []
    for r in cat.to_dicts():
        units = int(r["price_usd"]); nanos = int(round((r["price_usd"] - units) * 1e9))
        name = f"{r['category_en'].replace('_', ' ').title()} {r['public_id'][:4]}".replace("'", "")
        rows.append(f"('{r['public_id']}', '{name}', 'Marketplace offer ({r['category_en']})', '{PICTURES[0]}', 'USD', {units}, {nanos}, "
                    f"'{r['category_en']}')")
    sql = ("insert into catalog.products (id, name, description, picture, price_currency_code, price_units, price_nanos, categories) values "
           + ",".join(rows) + " on conflict (id) do update set name=excluded.name, description=excluded.description, "
           "price_units=excluded.price_units, price_nanos=excluded.price_nanos, categories=excluded.categories;")
    out = subprocess.run(["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", "astronomy_db", "-v", "ON_ERROR_STOP=1", "-q"],
                         input=sql, text=True, capture_output=True)
    if out.returncode:
        raise RuntimeError(f"catalog sync failed: {out.stderr[:300]}")
    return len(rows)


def _call(method, path, body=None, baggage="", timeout=20):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "baggage": baggage})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), time.perf_counter() - t
    except urllib.error.HTTPError as e:
        return e.code, None, time.perf_counter() - t
    except Exception:
        return -1, None, time.perf_counter() - t


class SessionDriver:
    """Executes plans; results are durable (results.jsonl) and keyed by command_id, so a replayed or
    retried plan is never executed twice (E5: the shop itself has no idempotency)."""

    def __init__(self, catalog: pl.DataFrame, results_path: pathlib.Path, person: dict, seed: int):
        self.cat, self.path, self.person, self.seed = catalog, pathlib.Path(results_path), person, seed
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.done = {}
        if self.path.exists():
            for line in open(self.path):
                r = json.loads(line)
                self.done[r["command_id"]] = r
        self.http_checkouts = 0
        w = self.cat["weight"].to_numpy()
        self._cum = np.cumsum(w) / w.sum()

    def product_for(self, order_idx: int) -> str:
        u = uniform(self.seed, "storefront:product", np.array([order_idx]))[0]
        return self.cat["public_id"][int(np.searchsorted(self._cum, u, side="right").clip(0, self.cat.height - 1))]

    def __call__(self, plans: list[dict]) -> dict[str, dict]:
        out = {}
        for p in plans:
            cid = p["command_id"]
            if cid in self.done:                                   # replay / retry: return the recorded result
                out[cid] = self.done[cid]
                continue
            r = self._execute(p)
            with open(self.path, "a") as f:
                f.write(json.dumps(r) + "\n")
            self.done[cid] = r
            out[cid] = r
        return out

    def _execute(self, p: dict) -> dict:
        if p.get("kind") == "browse":
            return self._browse(p)
        pid, user = self.product_for(p["order_idx"]), p["session_alias"]
        bag = f"session.id={user},synthetic_request=true,mkt.command_id={p['command_id']}"
        lat = {}
        c, _, lat["product"] = _call("GET", f"/api/products/{pid}?currencyCode=USD", baggage=bag)
        if c != 200:
            return {"command_id": p["command_id"], "outcome": "catalog_failed" if c > 0 else "timeout", "http": c, "latency_s": lat}
        c, _, lat["cart"] = _call("POST", "/api/cart", {"item": {"productId": pid, "quantity": 1}, "userId": user}, baggage=bag)
        if c != 200:
            return {"command_id": p["command_id"], "outcome": "cart_failed" if c > 0 else "timeout", "http": c, "latency_s": lat}
        person = dict(self.person, userId=user)
        self.http_checkouts += 1
        c, body, lat["checkout"] = _call("POST", "/api/checkout?currencyCode=USD", person, baggage=bag, timeout=30)
        if c == 200 and body and body.get("orderId"):
            return {"command_id": p["command_id"], "outcome": "ok", "storefront_order_id": body["orderId"], "product": pid, "latency_s": lat}
        outcome = {422: "payment_failed", -1: "timeout"}.get(c, "checkout_failed")
        return {"command_id": p["command_id"], "outcome": outcome, "http": c, "product": pid, "latency_s": lat}


    def _browse(self, p: dict) -> dict:
        """Browsing-only visit: n product views (keyed product choice), optional cart add, never a checkout."""
        user = p["session_alias"]
        bag = f"session.id={user},synthetic_request=true,mkt.command_id={p['command_id']}"
        lat, pids = {}, []
        for k in range(p["n_views"]):
            pid = self.product_for(p["order_idx"] * 8 + k)
            c, _, lat[f"view{k}"] = _call("GET", f"/api/products/{pid}?currencyCode=USD", baggage=bag)
            if c != 200:
                return {"command_id": p["command_id"], "outcome": "catalog_failed" if c > 0 else "timeout", "http": c, "latency_s": lat}
            pids.append(pid)
        if p["cart"] and pids:
            c, _, lat["cart"] = _call("POST", "/api/cart", {"item": {"productId": pids[-1], "quantity": 1}, "userId": user}, baggage=bag)
            if c != 200:
                return {"command_id": p["command_id"], "outcome": "cart_failed" if c > 0 else "timeout", "http": c, "latency_s": lat}
        return {"command_id": p["command_id"], "outcome": "ok", "views": len(pids), "carted": bool(p["cart"]), "latency_s": lat}


class FlagAdapter:
    """Writes the flag file flagd watches (atomic rename) and reads the value back through the flag service."""

    def __init__(self, flag_file: pathlib.Path):
        self.file = pathlib.Path(flag_file)

    def set_variant(self, flag: str, variant: str, timeout_s: float = 20.0) -> bool:
        d = json.loads(self.file.read_text())
        if variant not in d["flags"][flag]["variants"]:
            raise ValueError(f"{flag} has no variant {variant!r}")
        d["flags"][flag]["defaultVariant"] = variant
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        os.replace(tmp, self.file)
        t_end = time.time() + timeout_s
        while time.time() < t_end:                                # read-back: did flagd pick it up?
            if self.read(flag) == variant:
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def read(flag: str) -> str | None:
        c, body, _ = _call("POST", "/flagservice/flagd.evaluation.v1.Service/ResolveAll", {})
        return (body or {}).get("flags", {}).get(flag, {}).get("variant") if c == 200 else None
