"""Products, sellers, and a daily inventory / purchase-order simulation.

The funnel table only has category-level columns (`active_listings`,
`out_of_stock_views`), but those are derived from product-level stock here, so
a stock-out scenario has a real mechanism behind it: popular products run out,
because a supplier delivery was late or a purchase order was too small.

Randomness is drawn up front in fixed-shape arrays, so changing a hook (for
example delaying one delivery) changes only the products it touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy import stats

from .config import SimConfig

MARKETPLACE_SELLER_ID = "S000"  # the marketplace itself, buying wholesale (1P)


@dataclass
class InventoryHooks:
    """Optional callbacks the scenario injector can use to break the supply chain.

    delivery_delay(product_row, order_date) -> extra days a purchase order
        placed on `order_date` arrives late (0 = on time).
    order_qty_factor(product_row, order_date) -> multiplier on the ordered
        quantity (1.0 = normal, 0.3 = the purchasing system ordered too little).
    """

    delivery_delay: Optional[Callable[[pd.Series, pd.Timestamp], int]] = None
    order_qty_factor: Optional[Callable[[pd.Series, pd.Timestamp], float]] = None


def make_catalog(cfg: SimConfig, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (sellers, products)."""
    seller_ids = [f"S{i:03d}" for i in range(1, cfg.sellers + 1)]
    # each third-party seller focuses on one or two categories
    seller_cats = {}
    for sid in seller_ids:
        k = 1 + int(rng.random() < 0.4)
        seller_cats[sid] = list(rng.choice(cfg.categories, size=k, replace=False))
    sellers = pd.DataFrame(
        [{"seller_id": MARKETPLACE_SELLER_ID, "fulfilment": "1p", "categories": "*"}]
        + [{"seller_id": s, "fulfilment": "3p", "categories": "|".join(c)} for s, c in seller_cats.items()]
    )

    rows = []
    for cat in cfg.categories:
        candidates = [s for s, cats in seller_cats.items() if cat in cats] or seller_ids
        ranks = np.arange(1, cfg.products_per_category + 1)
        pop = 1.0 / ranks**cfg.popularity_zipf_s
        pop = pop / pop.sum()
        mean_price = cfg.aov_eur[cat] / cfg.units_per_order
        prices = mean_price * rng.lognormal(-0.18, 0.6, size=len(ranks))
        for i, (p, price) in enumerate(zip(pop, prices)):
            first_party = rng.random() < cfg.first_party_share
            rows.append(
                {
                    "product_id": f"{cat[:3].upper()}-{i + 1:04d}",
                    "category": cat,
                    "popularity_rank": i + 1,
                    "popularity": float(p),  # share of category demand / views
                    "seller_id": MARKETPLACE_SELLER_ID if first_party else str(rng.choice(candidates)),
                    "fulfilment": "1p" if first_party else "3p",
                    "price_eur": round(float(price), 2),
                }
            )
    return sellers, pd.DataFrame(rows)


def simulate_inventory(
    products: pd.DataFrame,
    daily_units: pd.DataFrame,
    cfg: SimConfig,
    rng: np.random.Generator,
    hooks: Optional[InventoryHooks] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate stock per product per day with a reorder-point policy.

    daily_units: index = dates (UTC midnight), columns = categories, values =
    expected units demanded in that category that day.

    Returns (inventory_daily, purchase_orders).
    """
    hooks = hooks or InventoryHooks()
    dates = pd.DatetimeIndex(daily_units.index)
    n_days, n_prod = len(dates), len(products)
    pop = products["popularity"].to_numpy()
    cats = products["category"].to_numpy()

    # expected demand per product per day, and the planning forecast
    mean_demand = np.column_stack([daily_units[c].to_numpy() for c in cats]) * pop  # (days, products)
    # Planners review best-sellers closely, so their forecasts are more accurate.
    top = products["popularity_rank"].to_numpy() <= max(1, int(round(cfg.top_seller_share * cfg.products_per_category)))
    err_sigma = np.where(top, cfg.forecast_error * cfg.top_seller_error_factor, cfg.forecast_error)
    forecast = mean_demand.mean(axis=0) * rng.lognormal(0.0, 1.0, size=n_prod) ** err_sigma
    lead = np.array([cfg.lead_time_days[f] for f in products["fulfilment"]])
    reorder_point = forecast * (lead + cfg.safety_stock_days)
    base_qty = np.ceil(forecast * cfg.order_cover_days)

    # all randomness drawn up front (fixed shapes)
    u_demand = np.clip(rng.random((n_days, n_prod)), 1e-12, 1 - 1e-12)
    demand_shock = rng.lognormal(-0.5 * cfg.demand_noise**2, cfg.demand_noise, size=(n_days, n_prod))
    jitter = rng.integers(0, cfg.lead_time_jitter_days + 1, size=(n_days, n_prod))
    start_fill = rng.random(n_prod)

    stock = np.round(reorder_point + start_fill * base_qty)
    arrivals = np.zeros((n_days + 60, n_prod))  # room for very late deliveries
    on_order = np.zeros(n_prod)
    demand = stats.poisson.ppf(u_demand, mean_demand * demand_shock)

    inv_records = []
    po_records = []
    for d in range(n_days):
        received = arrivals[d].copy()
        stock = stock + received
        on_order = on_order - received
        stock_start = stock.copy()

        sold = np.minimum(stock, demand[d])
        stock = stock - sold
        availability = np.where(demand[d] > 0, np.minimum(1.0, stock_start / np.maximum(demand[d], 1)), 1.0)
        availability = np.where(stock_start > 0, availability, 0.0)

        need = (stock + on_order) <= reorder_point
        for j in np.flatnonzero(need):
            prod = products.iloc[j]
            qty_factor = hooks.order_qty_factor(prod, dates[d]) if hooks.order_qty_factor else 1.0
            extra = int(hooks.delivery_delay(prod, dates[d])) if hooks.delivery_delay else 0
            qty = max(1.0, np.round(base_qty[j] * qty_factor))
            expected_lead = int(lead[j])
            actual_lead = expected_lead + int(jitter[d, j]) + extra
            arrive = d + max(1, actual_lead)
            if arrive < arrivals.shape[0]:
                arrivals[arrive, j] += qty
            on_order[j] += qty
            po_records.append(
                {
                    "po_id": f"PO-{len(po_records) + 1:05d}",
                    "product_id": prod["product_id"],
                    "category": prod["category"],
                    "seller_id": prod["seller_id"],
                    "fulfilment": prod["fulfilment"],
                    "order_date": dates[d],
                    "quantity": int(qty),
                    "expected_arrival": dates[d] + pd.Timedelta(days=expected_lead),
                    # unknown (NaT) if the delivery lands after the simulated period
                    "actual_arrival": dates[d] + pd.Timedelta(days=arrive - d) if arrive < n_days else pd.NaT,
                }
            )

        inv_records.append(
            pd.DataFrame(
                {
                    "date": dates[d],
                    "product_id": products["product_id"].to_numpy(),
                    "category": cats,
                    "stock_start": stock_start.astype(int),
                    "received": received.astype(int),
                    "demand_units": demand[d].astype(int),
                    "units_sold": sold.astype(int),
                    "lost_units": (demand[d] - sold).astype(int),
                    "stock_end": stock.astype(int),
                    "on_order": on_order.astype(int),
                    "availability": availability,
                }
            )
        )

    inventory = pd.concat(inv_records, ignore_index=True)
    pos = pd.DataFrame(
        po_records,
        columns=[
            "po_id", "product_id", "category", "seller_id", "fulfilment",
            "order_date", "quantity", "expected_arrival", "actual_arrival",
        ],
    )
    return inventory, pos


def category_availability(inventory: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Per (date, category): number of listed products and the share of
    (popularity-weighted) product views that land on out-of-stock items."""
    inv = inventory.merge(products[["product_id", "popularity"]], on="product_id")
    inv["oos_weight"] = inv["popularity"] * (1.0 - inv["availability"])
    inv["listed"] = inv["stock_start"] > 0
    out = inv.groupby(["date", "category"]).agg(
        active_listings=("listed", "sum"),
        oos_view_share=("oos_weight", "sum"),
    )
    return out.reset_index()
