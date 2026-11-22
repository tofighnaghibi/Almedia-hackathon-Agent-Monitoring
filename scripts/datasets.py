#!/usr/bin/env python3
"""Retrieve and audit every dataset in the stitching design (docs/stitching.excalidraw).

    python -I scripts/datasets.py plan                  # what is present / missing / blocked
    python -I scripts/datasets.py fetch [names...]      # download into ~/data/mkt/raw/<name>/
    python -I scripts/datasets.py audit [names...]      # check the data types the design assumes
    python -I scripts/datasets.py report                # docs/datasets/AUDIT.md + audit.json

The audit checks *claims*: for each stitching row a dataset is meant to back, it tests whether
the needed fields and semantics are really there, and records PASS / PARTIAL / FAIL / BLOCKED with
measured evidence. Downloads are untrusted data: each dataset gets its own directory, archives
are listed before extraction, nothing inside them is executed.

Env: KAGGLE_API_TOKEN for Kaggle sources. Data root: $MKT_RAW (default ~/data/mkt/raw).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Callable

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = pathlib.Path(os.environ.get("MKT_RAW", "~/data/mkt/raw")).expanduser()
CACHE = pathlib.Path("~/.cache/mkt-probe/data").expanduser()          # earlier probe downloads (reused, re-hashed)
DOWNLOADS = pathlib.Path("~/Downloads").expanduser()
OUT = ROOT / "docs" / "datasets"
KAGGLE = shutil.which("kaggle") or str(pathlib.Path(sys.executable).parent / "kaggle")
HF = "https://huggingface.co/datasets"
REVIEWS_CATEGORIES: list[str] = []   # 21 Nov: only the category map is used (review text comes from Olist, R2); category files archived


# ----------------------------------------------------------------------------- results
@dataclass
class Claim:
    row: str            # stitching row (data type) this claim backs
    role: str           # primary | secondary
    claim: str
    status: str = "BLOCKED"     # PASS | PARTIAL | FAIL | BLOCKED
    evidence: str = ""


@dataclass
class Dataset:
    name: str
    title: str
    source: str
    licence: str
    fetch: Callable[["Dataset"], str]
    audit: Callable[["Dataset"], list[Claim]]
    note: str = ""
    claims: list[Claim] = field(default_factory=list)

    @property
    def dir(self) -> pathlib.Path:
        return RAW / self.name


class Blocked(Exception):
    pass


def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def one(c, sql):
    return c.execute(sql).fetchone()


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(1 << 22):
            h.update(b)
    return h.hexdigest()


def http(url: str, dest: pathlib.Path) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "mkt-dataset-fetch/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 22)
    tmp.rename(dest)
    return dest


def unzip(z: pathlib.Path, to: pathlib.Path) -> list[str]:
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        bad = [n for n in names if n.startswith("/") or ".." in pathlib.PurePosixPath(n).parts]
        if bad:
            raise RuntimeError(f"unsafe paths in {z.name}: {bad[:3]}")
        zf.extractall(to)
    return names


def kaggle(args: list[str], to: pathlib.Path) -> None:
    to.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([KAGGLE, *args, "-p", str(to)], capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
        raise Blocked(f"kaggle {' '.join(args)}: {msg[0][:200]}")
    for z in to.glob("*.zip"):
        unzip(z, to)
        z.unlink()


def copy_from(src: pathlib.Path, dst: pathlib.Path) -> bool:
    if not src.exists():
        return False
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(src, dst)
    return True


def git_sparse(url: str, to: pathlib.Path, paths: list[str]) -> None:
    if (to / ".git").exists():
        return
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", url, str(to)], check=True)
    subprocess.run(["git", "-C", str(to), "sparse-checkout", "set", *paths], check=True)


def P(row, role, claim, status, evidence):
    return Claim(row, role, claim, status, evidence)


def pct(x):
    return f"{100 * x:.1f}%"


# ----------------------------------------------------------------------------- fetchers
def f_olist(d):
    if not copy_from(CACHE / "olist", d.dir):
        kaggle(["datasets", "download", "olistbr/brazilian-ecommerce"], d.dir)
    if not (d.dir / "funnel").exists():
        kaggle(["datasets", "download", "olistbr/marketing-funnel-olist"], d.dir / "funnel")
    return "kaggle olistbr/brazilian-ecommerce (+ marketing-funnel-olist)"


def f_funnel(d):
    if not copy_from(CACHE / "olist" / "funnel", d.dir):
        kaggle(["datasets", "download", "olistbr/marketing-funnel-olist"], d.dir)
    return "kaggle olistbr/marketing-funnel-olist"


def f_retailrocket(d):
    if not copy_from(CACHE / "retailrocket", d.dir):
        kaggle(["datasets", "download", "retailrocket/ecommerce-dataset"], d.dir)
    return "kaggle retailrocket/ecommerce-dataset"


def f_m2(d):
    official = [DOWNLOADS / "products_train.csv", DOWNLOADS / "sessions_train.csv"]
    if all(p.exists() for p in official):
        for p in official:
            copy_from(p, d.dir / p.name)
        return "AIcrowd official (manual download), byte-identical to Kaggle mirror marquis03/amazon-m2"
    if copy_from(CACHE / "m2", d.dir):
        return "Kaggle mirror marquis03/amazon-m2 (verified identical to the official release)"
    kaggle(["datasets", "download", "marquis03/amazon-m2"], d.dir)
    return "Kaggle mirror marquis03/amazon-m2"


def f_esci(d):
    base = "https://github.com/amazon-science/esci-data/raw/main/shopping_queries_dataset/"
    for f in ("shopping_queries_dataset_examples.parquet", "shopping_queries_dataset_products.parquet",
              "shopping_queries_dataset_sources.csv"):
        http(base + f, d.dir / f)
    return "github amazon-science/esci-data (official)"


def f_reviews(d):
    copy_from(CACHE / "asin2category.json", d.dir / "asin2category.json") or \
        http(f"{HF}/McAuley-Lab/Amazon-Reviews-2023/resolve/main/asin2category.json", d.dir / "asin2category.json")
    for cat in REVIEWS_CATEGORIES:
        for kind, sub in (("", "review_categories"), ("meta_", "meta_categories")):
            name = f"{kind}{cat}.jsonl"
            if not copy_from(CACHE / name, d.dir / name):
                http(f"{HF}/McAuley-Lab/Amazon-Reviews-2023/resolve/main/raw/{sub}/{name}", d.dir / name)
    return "HF McAuley-Lab/Amazon-Reviews-2023: category map only (asin2category.json); category files archived 21 Nov"


def f_open_bandit(d):
    for pol in ("random", "bts"):
        for f in ("all.csv", "item_context.csv"):
            http(f"{HF}/zozonext/open-bandit/resolve/main/{pol}/all/{f}", d.dir / pol / f)
    return "HF zozonext/open-bandit (random + bts, all campaigns)"


def f_criteo(d):
    http(f"{HF}/criteo/criteo-uplift/resolve/main/criteo-research-uplift-v2.1.csv.gz", d.dir / "criteo-research-uplift-v2.1.csv.gz")
    return "HF criteo/criteo-uplift v2.1"


BCB_SERIES = {"ipca_monthly_pct": 433, "selic_target_pct": 432, "usd_brl": 1}


def f_bcb(d):
    for name, code in BCB_SERIES.items():
        http(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json&dataInicial=01/01/2016&dataFinal=31/12/2018",
             d.dir / f"{name}.json")
    return "api.bcb.gov.br SGS series 433, 432, 1 (2016-2018)"


def f_none(d):
    d.dir.mkdir(parents=True, exist_ok=True)
    return "computed / documentary (nothing to download)"


def f_holidays_ref(d):
    """Reference file: Brazil national holidays + shopping events 2016-2018, generated by the rules in stitch.py."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from stitch import _easter
    rows = []
    for y in (2016, 2017, 2018):
        e = _easter(y)
        for (m, dd), n in {(1, 1): "new_year", (4, 21): "tiradentes", (5, 1): "labour_day", (9, 7): "independence",
                           (10, 12): "our_lady_aparecida", (11, 2): "all_souls", (11, 15): "republic", (12, 25): "christmas"}.items():
            rows.append((dt.date(y, m, dd), n, "national_holiday"))
        rows.append((e - dt.timedelta(days=2), "good_friday", "national_holiday"))
        for off, n in ((-48, "carnival_monday"), (-47, "carnival_tuesday"), (60, "corpus_christi")):
            rows.append((e + dt.timedelta(days=off), n, "optional_holiday"))
        nov1 = dt.date(y, 11, 1)
        rows.append((nov1 + dt.timedelta(days=(4 - nov1.weekday()) % 7 + 21), "black_friday", "shopping_event"))
    d.dir.mkdir(parents=True, exist_ok=True)
    out = d.dir / "holidays_br_2016_2018.csv"
    out.write_text("date,name,type\n" + "".join(f"{a},{b},{c}\n" for a, b, c in sorted(rows)))
    return "reference file generated from rules (scripts/stitch.py _easter + fixed dates); checked against python-holidays"


def f_strike_ref(d):
    """Reference file: 2018 truckers' strike window with sources."""
    d.dir.mkdir(parents=True, exist_ok=True)
    (d.dir / "truckers_strike_2018.json").write_text(json.dumps({
        "event": "Brazil truck drivers' strike", "start": "2018-05-21", "effective_end": "2018-06-01", "formal_end": "2018-06-02",
        "sources": ["https://en.wikipedia.org/wiki/2018_Brazil_truck_drivers%27_strike",
                    "https://stabroeknews.com/2018/06/01/news/regional/brazil-returns-to-uneasy-calm-as-protests-wind-down",
                    "https://crisis24.garda.com/fr/product/247296",
                    "https://wsws.org/en/articles/2018/06/04/braz-j04.html"],
        "verified": "2026-11-21; Olist orders drop 1,805 -> 941 in the week of 21 May"}, indent=1))
    return "reference file: documented strike window with source links"


def f_m5(d):
    kaggle(["competitions", "download", "m5-forecasting-accuracy"], d.dir)
    return "kaggle competition m5-forecasting-accuracy"


def f_instacart(d):
    manual = DOWNLOADS / "archive.zip"
    if manual.exists():
        with zipfile.ZipFile(manual) as zf:
            names = set(zf.namelist())
        need = {"orders.csv", "order_products__prior.csv", "order_products__train.csv", "products.csv", "aisles.csv", "departments.csv"}
        if need <= names:
            copy_from(manual, d.dir / "instacart_archive.zip")
            unzip(d.dir / "instacart_archive.zip", d.dir)
            (d.dir / "instacart_archive.zip").unlink()
            return "Kaggle copy (manual download by user, ~/Downloads/archive.zip, 207,073,669 bytes = psparks/instacart-market-basket-analysis size); original competition page is gone (404)"
    try:
        kaggle(["competitions", "download", "instacart-market-basket-analysis"], d.dir)
        return "kaggle competition instacart-market-basket-analysis"
    except Blocked as e:
        raise Blocked(f"{e}. The competition is not reachable via the API; a dataset copy would be a third-party mirror (decision needed)")


def f_uci(d):
    z = http("https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip", d.dir / "online_retail_ii.zip")
    unzip(z, d.dir)
    return "UCI ML repository #502"


def f_yelpchi(d):
    z = http("https://github.com/YingtongDou/CARE-GNN/raw/master/data/YelpChi.zip", d.dir / "YelpChi.zip")
    unzip(z, d.dir)
    return "github YingtongDou/CARE-GNN data/YelpChi.zip (graph version of Rayana & Akoglu 2015)"


def f_amazon_fraud(d):
    z = http("https://github.com/YingtongDou/CARE-GNN/raw/master/data/Amazon.zip", d.dir / "Amazon.zip")
    unzip(z, d.dir)
    return "github YingtongDou/CARE-GNN data/Amazon.zip (McAuley & Leskovec 2013 users, fraud labels)"


def f_coat(d):
    z = http("https://www.cs.cornell.edu/~schnabts/mnar/coat.zip", d.dir / "coat.zip")
    unzip(z, d.dir)
    return "Cornell (Schnabel et al. 2016) coat.zip"


def f_yahoo(d):
    raise Blocked("Yahoo! Webscope R3 requires an application and approval (site did not respond); needs your action")


def f_ipinyou(d):
    kaggle(["datasets", "download", "lastsummer/ipinyou"], d.dir)
    return "Kaggle mirror lastsummer/ipinyou (third-party copy of the iPinYou RTB release)"


def f_hillstrom(d):
    http("http://www.minethatdata.com/Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv",
         d.dir / "hillstrom.csv")
    return "minethatdata.com (Kevin Hillstrom, 2008)"


def f_nab_loghub(d):
    git_sparse("https://github.com/numenta/NAB.git", d.dir / "NAB", ["data", "labels"])
    git_sparse("https://github.com/logpai/loghub.git", d.dir / "loghub", ["HDFS", "OpenStack", "Apache", "Linux"])
    return "github numenta/NAB (data, labels) + logpai/loghub (2k-line samples)"


def f_astronomy(d):
    src = pathlib.Path("~/.cache/otel-demo").expanduser()
    if not (src / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/open-telemetry/opentelemetry-demo.git", str(src)], check=True)
    d.dir.mkdir(parents=True, exist_ok=True)
    (d.dir / "SOURCE.txt").write_text(f"{src}\n")
    return "github open-telemetry/opentelemetry-demo (pinned clone)"


def f_tmall(d):
    manual = next((p for p in DOWNLOADS.glob("data_format1*.zip")), None)
    if not manual:
        raise Blocked("download data_format1.zip from https://tianchi.aliyun.com/dataset/42 into ~/Downloads")
    if not (d.dir / "data_format1" / "user_log_format1.csv").exists():
        d.dir.mkdir(parents=True, exist_ok=True)
        unzip(manual, d.dir)
    return f"Tianchi dataset 42 (IJCAI-15 Tmall repeat buyers), data_format1, manual download {manual.name!r}"


def f_taobao(d):
    z = DOWNLOADS / "UserBehavior.csv.zip"
    md5f = DOWNLOADS / "UserBehavior.csv.zip.md5"
    if not z.exists() or z.stat().st_size == 0:
        raise Blocked("UserBehavior.csv.zip not (fully) downloaded yet from https://tianchi.aliyun.com/dataset/649")
    want = md5f.read_text().split("=")[-1].strip() if md5f.exists() else None
    h = hashlib.md5()
    with open(z, "rb") as f:
        while b := f.read(1 << 22):
            h.update(b)
    if want and h.hexdigest() != want:
        raise Blocked(f"md5 mismatch: got {h.hexdigest()}, Tianchi says {want} (download incomplete or corrupted)")
    if not (d.dir / "UserBehavior.csv").exists():
        d.dir.mkdir(parents=True, exist_ok=True)
        unzip(z, d.dir)
    return f"Tianchi dataset 649 (Taobao User Behavior), manual download, md5 {'verified' if want else 'not available'}"


def f_expedia(d):
    if not (d.dir / "train.csv").exists():
        kaggle(["competitions", "download", "expedia-personalized-sort", "-f", "data.zip"], d.dir)
        for z in d.dir.glob("*.zip"):          # data.zip may itself contain train.zip
            unzip(z, d.dir)
            z.unlink()
    return "kaggle competition expedia-personalized-sort (data.zip: train.csv)"


def f_ga(d):
    if not (d.dir / "train.csv").exists():
        kaggle(["competitions", "download", "ga-customer-revenue-prediction", "-f", "train.csv"], d.dir)
    return "kaggle competition ga-customer-revenue-prediction (train.csv v1 only; 25 GB v2 not needed)"


def f_statcounter(d):
    url = ("https://gs.statcounter.com/platform-market-share/desktop-mobile-tablet/brazil/chart.php?device=Desktop%20%26%20Mobile%20%26%20Tablet"
           "&device_hidden=desktop%2Bmobile%2Btablet&multi-device=true&statType_hidden=comparison&region_hidden=BR&granularity=monthly"
           "&statType=Platform%20Comparison&region=Brazil&fromInt=201601&toInt=201812&fromMonthYear=2016-01&toMonthYear=2018-12&csv=1")
    http(url, d.dir / "brazil_device_share_2016_2018.csv")
    return "gs.statcounter.com: Brazil desktop/mobile/tablet share, monthly 2016-2018 (CSV export)"


def f_keepa(d):
    raise Blocked("DROPPED 21 Nov 2026: paid source (decision: drop anything paid)")


# ----------------------------------------------------------------------------- audits
def a_olist(d):
    p = d.dir
    c = con()
    for t in ["orders", "order_items", "order_payments", "order_reviews", "customers", "sellers", "products"]:
        c.execute(f"create view {t} as select * from read_csv_auto('{p}/olist_{t}_dataset.csv')")
    c.execute(f"create view cat as select * from read_csv_auto('{p}/product_category_name_translation.csv')")
    o = one(c, "select count(*) from orders")[0]
    orph = one(c, """select (select count(*) from order_items anti join orders using(order_id)) +
                     (select count(*) from order_payments anti join orders using(order_id)) +
                     (select count(*) from order_reviews anti join orders using(order_id))""")[0]
    ts = one(c, """select avg((order_approved_at is not null)::int), avg((order_delivered_carrier_date is not null)::int),
                   avg((order_delivered_customer_date is not null)::int), avg((order_estimated_delivery_date is not null)::int) from orders""")
    late = c.execute("""select case when date_diff('day', o.order_estimated_delivery_date, o.order_delivered_customer_date) <= 0
                        then 'on_time' else 'late' end b, avg(r.review_score) from orders o join order_reviews r using(order_id)
                        where o.order_delivered_customer_date is not null group by 1 order by 1""").fetchall()
    late = dict(late)
    text = one(c, "select avg((review_comment_message is not null)::int) from order_reviews")[0]
    rep = one(c, """select avg((n>1)::int) from (select customer_unique_id, count(*) n from orders join customers using(customer_id) group by 1)""")[0]
    sel = one(c, "select count(*), avg((seller_state is not null)::int) from sellers")
    buy = one(c, "select count(distinct customer_state) from customers")[0]
    catcov = one(c, """select avg((t.product_category_name_english is not null)::int) from order_items i join products p using(product_id)
                       left join cat t using(product_category_name)""")[0]
    multi_price = one(c, "select avg((k>1)::int) from (select product_id, count(distinct price) k from order_items group by 1)")[0]
    multi_seller = one(c, "select avg((s>1)::int) from (select product_id, count(distinct seller_id) s from order_items group by 1)")[0]
    st = dict(c.execute("select order_status, count(*) from orders group by 1").fetchall())
    same_week = one(c, """with x as (select i.product_id p, i.seller_id s, date_trunc('week', o.order_purchase_timestamp) w
                              from order_items i join orders o using(order_id)),
                          m as (select p from x group by 1 having count(distinct s) > 1)
                          select (select count(*) from m),
                                 (select count(distinct p) from (select p from x where p in (select p from m) group by p, w having count(distinct s) > 1))""")
    bf = one(c, """with d as (select cast(order_purchase_timestamp as date) dd, count(*) n from orders
                   where order_purchase_timestamp between '2017-10-15' and '2017-12-15' group by 1)
                   select max(n) / median(n) from d""")[0]
    strike = one(c, """select (select count(*) from orders where order_purchase_timestamp >= '2018-05-21' and order_purchase_timestamp < '2018-05-28'),
                              (select count(*) from orders where order_purchase_timestamp >= '2018-05-14' and order_purchase_timestamp < '2018-05-21')""")
    anomalies = one(c, "select sum((order_delivered_carrier_date < order_approved_at)::int) from orders")[0]
    ex = one(c, """with s as (select i.seller_id, min(o.order_purchase_timestamp) f, max(o.order_purchase_timestamp) l, count(*) n
                   from order_items i join orders o using(order_id) group by 1)
                   select avg((l < '2018-05-01')::int), avg((l < '2018-05-01')::int) filter (where n <= 5),
                          avg((l < '2018-05-01')::int) filter (where n > 50) from s where f < '2018-01-01'""")
    reply = one(c, """select median(h), avg((review_answer_timestamp is not null)::int), quantile_cont(h, 0.9) from
                      (select *, date_diff('minute', review_creation_date, review_answer_timestamp) / 60.0 h from order_reviews)""")
    return [
        P("Orders, items, payments", "primary", "order/item/payment tables joined by order_id", "PASS" if orph == 0 else "PARTIAL", f"{o:,} orders; {orph} orphan rows"),
        P("Delivery promise, delays", "primary", "purchase, approval, carrier, delivered and estimated timestamps", "PASS",
          f"non-null: approved {pct(ts[0])}, carrier {pct(ts[1])}, delivered {pct(ts[2])}, estimate {pct(ts[3])}"),
        P("Handling time, reliability", "primary", "approval -> carrier handover per seller", "PASS", f"carrier date present {pct(ts[1])}"),
        P("Review score given delivery", "primary", "1-5 score linked to delivery outcome", "PASS" if late.get("late", 5) < late.get("on_time", 0) else "FAIL",
          f"mean score on time {late.get('on_time', 0):.2f} vs late {late.get('late', 0):.2f}"),
        P("Review text", "primary", "free-text review comments", "PASS", f"{pct(text)} of reviews have text (Portuguese)"),
        P("Who leaves reviews (MNAR)", "primary", "review presence observable per order", "PARTIAL",
          "presence observable, but ~99% of orders have a review (survey-driven), so self-selection is weak; Coat/Yahoo needed for MNAR shape"),
        P("Repeat purchases", "primary", "stable buyer id across orders", "PASS", f"customer_unique_id; repeat-buyer share {pct(rep)}"),
        P("Sellers, regions, size", "primary", "seller ids with region and sales volume", "PASS", f"{sel[0]:,} sellers; state present {pct(sel[1])}"),
        P("Buyers, regions", "primary", "buyer region", "PASS", f"{buy} Brazilian states"),
        P("Products, categories, titles", "primary", "product categories (no titles)", "PARTIAL",
          f"category coverage {pct(catcov)}; NO product titles or brands (names removed) -> titles must come from ESCI/Reviews bridge"),
        P("Prices, promotions", "primary", "price per sold item, variation over time", "PARTIAL",
          f"price on every item; {pct(multi_price)} of products sold at >1 price; no list prices or promotion flags"),
        P("Multi-seller offers", "primary", "same product sold by several sellers AT THE SAME TIME", "PARTIAL",
          f"{pct(multi_seller)} of products had >1 seller over the whole period, but only {same_week[1]} of {same_week[0]} had two sellers "
          "selling in the same week -> mostly sequential, not simultaneous competition; displayed offers and rankings are synthetic"),
        P("Cancellations, returns", "primary", "cancellation and return records", "PARTIAL",
          f"canceled {st.get('canceled', 0)}, unavailable {st.get('unavailable', 0)}; NO return records"),
        P("Seasonality, holidays, Black Friday", "primary", "daily order series with seasonal peaks", "PASS", f"Black Friday peak {bf:.1f}x Oct-Dec median"),
        P("External shocks (2018 strike)", "primary", "visible shock in strike week", "PASS",
          f"orders {strike[1]} (week of 14 May) -> {strike[0]} (week of 21 May)"),
        P("Traffic volume, calendar", "primary", "volume level and calendar profile", "PARTIAL",
          "orders only: no sessions/visits, so traffic level is inferred via the funnel (anchor step)"),
        P("Logging gaps, delays, late data", "secondary", "timestamp anomalies as evidence of measurement issues", "PASS",
          f"{anomalies} orders handed to carrier before approval"),
        P("Seller entry, ramp-up", "secondary", "first sale per seller", "PASS", "first order date per seller_id derivable"),
        P("Seller exit", "primary", "sellers that stop selling, by size", "PARTIAL",
          f"{pct(ex[0])} of sellers active before 2018 had no sale after May 2018 (small <=5 sales {pct(ex[1])}, large >50 {pct(ex[2])}); "
          "data ends Aug 2018, so quiet occasional sellers look like exits (censoring)"),
        P("Review timing (survey sent, answered)", "primary", "when the satisfaction survey is sent and when the CUSTOMER answers it",
          "PASS" if reply[1] > 0.9 else "PARTIAL",
          f"survey sent at delivery or at the estimated date (Olist description); answer timestamp present {pct(reply[1])}; "
          f"customer answers after median {reply[0]:.1f} h, p90 {reply[2]:.1f} h. NOT a seller reply (corrected 21 Nov after external review)"),
    ]


def a_funnel(d):
    c = con()
    deals = d.dir / "olist_closed_deals_dataset.csv"
    mql = d.dir / "olist_marketing_qualified_leads_dataset.csv"
    items = RAW / "olist" / "olist_order_items_dataset.csv"
    n, joined = one(c, f"""select count(*), (select count(distinct d.seller_id) from read_csv_auto('{deals}') d
                            semi join read_csv_auto('{items}') i using(seller_id)) from read_csv_auto('{deals}')""")
    cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{mql}')").fetchall()]
    return [P("Seller entry, ramp-up", "primary", "acquisition date + channel, joinable to sales", "PASS",
              f"{n} closed deals, {joined} join to Olist sales on seller_id; MQL columns {cols}"),
            P("Sellers, regions, size", "secondary", "seller segment / type", "PASS", "business_segment, lead_type, business_type columns")]


def a_retailrocket(d):
    c = con()
    ev = d.dir / "events.csv"
    ip = [str(d.dir / "item_properties_part1.csv"), str(d.dir / "item_properties_part2.csv")]
    cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{ev}')").fetchall()]
    types = dict(c.execute(f"select event, count(*) from read_csv_auto('{ev}') group by 1").fetchall())
    span = one(c, f"select to_timestamp(min(timestamp)/1000), to_timestamp(max(timestamp)/1000) from read_csv_auto('{ev}')")
    c.execute(f"create view ip as select * from read_csv_auto({ip})")
    av = one(c, "select count(distinct itemid), count(distinct value) from ip where property = 'available'")
    pr = one(c, """select count(*), avg((regexp_matches(value, '^n[0-9.]+$'))::int),
                   (select avg((k>1)::int) from (select itemid, count(distinct value) k from ip where property='790' group by 1))
                   from ip where property = '790'""")
    noview = one(c, f"""with t as (select visitorid v, itemid i, min(timestamp) tt from read_csv_auto('{ev}') where event='transaction' group by 1,2),
                       w as (select visitorid v, itemid i, min(timestamp) tv from read_csv_auto('{ev}') where event='view' group by 1,2)
                       select avg((w.tv is null or w.tv > t.tt)::int) from t left join w using(v, i)""")[0]
    return [
        P("Funnel view -> cart -> buy, timing", "primary", "typed events with ms timestamps", "PASS", f"columns {cols}; {types}; {span[0]:%Y-%m-%d} .. {span[1]:%Y-%m-%d}"),
        P("Inventory, stock-outs", "primary", "time-varying availability flag", "PASS", f"'available' on {av[0]:,} items, values {av[1]} (snapshot-based, weekly-ish)"),
        P("Prices, promotions", "secondary", "price history (property 790 assumed = price)", "PARTIAL",
          f"{pr[0]:,} snapshots, {pct(pr[1])} numeric 'n…' format, {pct(pr[2])} of items change value; meaning 'price' is inferred, not documented (hashed properties)"),
        P("Session length, revisits", "secondary", "events per visitor-day", "PASS", "visitor id + timestamps give visitor-day sessions (no explicit session id)"),
        P("Traffic volume, calendar", "secondary", "hourly traffic pattern", "PASS", "event timestamps (UTC ms)"),
        P("Buyers, regions", "secondary", "buyer region", "FAIL", "no geography field at all (only visitorid) -> design must drop this secondary"),
        P("Logging gaps, delays, late data", "secondary", "purchases without a logged prior view", "PASS", f"{pct(noview)} of purchased (visitor,item) pairs have no earlier view"),
    ]


def a_m2(d):
    c = con()
    s = d.dir / "sessions_train.csv"
    cols = [r[0] for r in c.execute(f"describe select * from read_csv('{s}', header=true, parallel=false)").fetchall()]
    n, loc = one(c, f"select count(*), count(distinct locale) from read_csv('{s}', header=true, parallel=false)")
    pcols = [r[0] for r in c.execute(f"describe select * from read_csv('{d.dir}/products_train.csv', header=true, parallel=false)").fetchall()]
    return [
        P("Session length, revisits", "primary", "ordered item sequences per session", "PASS", f"{n:,} sessions, {loc} locales; columns {cols}"),
        P("Funnel view -> cart -> buy, timing", "secondary", "event types and timestamps", "FAIL",
          "no event types, no timestamps, no user ids -> cannot support funnel or timing; keep only for sequence length"),
        P("Products, categories, titles", "secondary", "(not in design, bonus) product attributes", "PASS", f"product columns {pcols}"),
    ]


def a_esci(d):
    c = con()
    ex, pr = d.dir / "shopping_queries_dataset_examples.parquet", d.dir / "shopping_queries_dataset_products.parquet"
    ecols = [r[0] for r in c.execute(f"describe select * from '{ex}'").fetchall()]
    pcols = [r[0] for r in c.execute(f"describe select * from '{pr}'").fetchall()]
    n = one(c, f"select count(*), count(distinct query_id), count(distinct product_id) from '{ex}'")
    mix = dict(c.execute(f"select esci_label, round(count(*)/(select count(*) from '{ex}'),3) from '{ex}' where product_locale='us' group by 1").fetchall())
    us = one(c, f"select count(*) from '{pr}' where product_locale='us'")[0]
    title = one(c, f"select avg((product_title is not null)::int), avg((product_brand is not null)::int) from '{pr}' where product_locale='us'")
    return [
        P("Search queries, relevance", "primary", "query text + human E/S/C/I labels per product", "PASS",
          f"{n[0]:,} judgements, {n[1]:,} queries, {n[2]:,} products; US label mix {mix}; columns {ecols}"),
        P("Products, categories, titles", "secondary", "product titles and brands", "PASS", f"US products {us:,}; title {pct(title[0])}, brand {pct(title[1])}; columns {pcols}"),
        P("Exposure, position, clicks", "secondary", "rank positions / exposure", "PARTIAL", "relevance labels only: no positions, impressions or clicks"),
    ]


def a_reviews(d):
    c = con()
    out = []
    rows = []
    for cat in REVIEWS_CATEGORIES:
        r, m = d.dir / f"{cat}.jsonl", d.dir / f"meta_{cat}.jsonl"
        if not r.exists():
            continue
        x = one(c, f"""select count(*), avg((text is not null and length(text)>0)::int), min(to_timestamp(timestamp/1000)),
                      max(to_timestamp(timestamp/1000)), avg((rating between 1 and 5)::int) from read_json_auto('{r}')""")
        y = one(c, f"select count(*), avg((price is not null)::int), avg((store is not null)::int), avg((title is not null)::int) from read_json_auto('{m}')")
        rows.append(f"{cat}: {x[0]:,} reviews {x[2]:%Y}-{x[3]:%Y}, text {pct(x[1])}; {y[0]:,} items, price {pct(y[1])}, title {pct(y[3])}")
    ev = "; ".join(rows)
    if not rows:   # category files archived: only the map is in use
        return [P("Search queries, relevance", "secondary", "category for each Amazon item (to place ESCI items in mapped categories)", "PASS",
                  "asin2category.json maps 35,393,189 parent ASINs to 34 categories (measured earlier); 36.8% of ESCI-US items covered. "
                  "Review/meta category files archived 21 Nov (audited earlier: ratings + English text present, no delivery fields)")]
    out += [P("Search queries, relevance", "secondary", "category for each Amazon item (to place ESCI items in mapped categories)", "PASS",
              "asin2category.json maps 35,393,189 parent ASINs to 34 categories (measured earlier); 36.8% of ESCI-US items covered")]
    out += [P("Review score given delivery", "secondary", "ratings with timestamps (no delivery info)", "PARTIAL", ev + " | no delivery outcome fields"),
            P("Review text", "secondary", "English review text", "PASS", ev),
            P("Products, categories, titles", "secondary", "item titles, store, categories, price", "PARTIAL", ev + " | price often missing")]
    return out


def a_open_bandit(d):
    c = con()
    res = []
    for pol in ("random", "bts"):
        f = d.dir / pol / "all.csv"
        cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{f}')").fetchall()]
        st = one(c, f"""select count(*), count(distinct position), min(propensity_score), max(propensity_score), avg(click),
                       min(timestamp), max(timestamp), count(distinct item_id) from read_csv_auto('{f}')""")
        res.append((pol, cols, st))
    (_, cols, r), (_, _, b) = res
    ctr = c.execute(f"select position, avg(click) from read_csv_auto('{d.dir}/random/all.csv') group by 1 order by 1").fetchall()
    return [
        P("Exposure, position, clicks", "primary", "position + click under a uniformly random policy; position effect present",
          "PASS" if (r[2] == r[3] and max(x for _, x in ctr) / max(min(x for _, x in ctr), 1e-9) >= 1.2) else "PARTIAL",
          f"random: {r[0]:,} rows, {r[1]} positions, {r[7]} items, propensity {r[2]:.5f}..{r[3]:.5f} (uniform, as required); "
          f"CTR by position {[(p, round(x, 4)) for p, x in ctr]} -> {'no meaningful position effect (likely because ZOZOTOWN shows the 3 slots together; unverified), so it cannot supply a search-ranking decay curve' if max(x for _, x in ctr) / max(min(x for _, x in ctr), 1e-9) < 1.2 else 'position effect present'}"),
        P("Recommendation policy, propensities", "primary", "logged propensities for two policies", "PASS",
          f"bts: {b[0]:,} rows, propensity {b[2]:.4g}..{b[3]:.4g}; columns {cols[:8]}…"),
        P("Experiments, assignments", "primary", "assignment probabilities known per impression", "PASS", f"time span {r[5]} .. {r[6]}"),
    ]


def a_criteo(d):
    c = con()
    f = d.dir / "criteo-research-uplift-v2.1.csv.gz"
    cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{f}')").fetchall()]
    n, tr, ex_c, ex_t = one(c, f"""select count(*), avg(treatment), avg(exposure) filter (where treatment=0), avg(exposure) filter (where treatment=1)
                                   from read_csv_auto('{f}')""")
    arms = c.execute(f"select treatment, avg(visit), avg(conversion) from read_csv_auto('{f}') group by 1 order by 1").fetchall()
    return [
        P("Ads, sponsored lift", "primary", "randomised ad treatment with exposure, visit, conversion", "PASS",
          f"{n:,} rows; treated {pct(tr)}; exposure control {ex_c:.4f} vs treated {ex_t:.4f}; visit/conv by arm {[(a, round(v, 4), round(cv, 5)) for a, v, cv in arms]}; columns {cols}"),
        P("Experiments, assignments", "secondary", "known assignment + non-compliance", "PASS", "treatment is randomised; exposure records non-compliance"),
    ]


def a_bcb(d):
    ev = []
    ok = True
    for name in BCB_SERIES:
        js = json.loads((d.dir / f"{name}.json").read_text())
        vals = [float(x["valor"]) for x in js]
        ev.append(f"{name}: {len(js)} obs {js[0]['data']}..{js[-1]['data']} range {min(vals):.2f}..{max(vals):.2f}")
        ok &= len(js) > 0
    plaus = "Selic should fall ~14 -> ~6.5; IPCA monthly within -0.5..1.5; USD/BRL ~3-4.2"
    return [P("Macro: inflation, FX, interest", "primary", "daily/monthly macro series 2016-2018", "PASS" if ok else "FAIL", "; ".join(ev) + f" | expected: {plaus}")]


def a_holidays(d):
    import holidays as hol
    sys.path.insert(0, str(ROOT / "scripts"))
    from stitch import _easter  # computed rules used by the stitching step
    ours = set()
    for y in (2016, 2017, 2018):
        e = _easter(y)
        ours |= {dt.date(y, m, dd) for m, dd in [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]}
        ours |= {e - dt.timedelta(days=2)}
    ref = {x for x in hol.Brazil(years=[2016, 2017, 2018])}
    miss, extra = sorted(ref - ours), sorted(ours - ref)
    return [P("Seasonality, holidays, Black Friday", "secondary", "national holiday calendar", "PASS" if not miss else "PARTIAL",
              f"vs python-holidays Brazil: missing {miss[:6]}, extra {extra[:6]} (carnival/corpus christi are optional points in the reference)")]


def a_public(d):
    return [P("External shocks (2018 strike)", "secondary", "documented strike window", "PASS",
              "21 May 2018 start; effectively over 31 May-1 Jun, formal end 2 Jun (Wikipedia, Reuters via Stabroek News 1 Jun, Garda alert 1 Jun)")]


def a_m5(d):
    c = con()
    cal, sp, sales = d.dir / "calendar.csv", d.dir / "sell_prices.csv", d.dir / "sales_train_evaluation.csv"
    ccols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{cal}')").fetchall()]
    ev = one(c, f"select count(*) filter (where event_name_1 is not null), count(*) from read_csv_auto('{cal}')")
    chg = one(c, f"""select avg((sell_price <> prev)::int) from (select sell_price, lag(sell_price) over (partition by store_id, item_id order by wm_yr_wk) prev
                    from read_csv_auto('{sp}')) where prev is not null""")[0]
    n_series = one(c, f"select count(*) from read_csv_auto('{sales}')")[0]
    return [
        P("Prices, promotions", "secondary", "weekly prices with changes", "PASS", f"weekly change probability {pct(chg)}; no explicit promo flag (drops inferred)"),
        P("Seasonality, holidays, Black Friday", "secondary", "calendar events + SNAP days", "PASS", f"{ev[0]} event days of {ev[1]}; calendar columns {ccols}"),
        P("Inventory, stock-outs", "secondary", "stock-outs", "PARTIAL", f"{n_series:,} item-store series; only zero-sales runs, no stock field (proxy)"),
    ]


def a_instacart(d):
    c = con()
    o = d.dir / "orders.csv"
    cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{o}')").fetchall()]
    q = one(c, f"select count(*), count(distinct user_id), median(days_since_prior_order) from read_csv_auto('{o}')")
    return [P("Repeat purchases", "secondary", "inter-purchase intervals", "PASS", f"{q[0]:,} orders, {q[1]:,} users, median gap {q[2]} days; columns {cols}; no absolute dates")]


def a_uci(d):
    import polars as pl
    f = next(d.dir.glob("*.xlsx"))
    sheets = []
    for s in ("Year 2009-2010", "Year 2010-2011"):
        sheets.append(pl.read_excel(f, sheet_name=s))
    df = pl.concat(sheets)
    inv = df["Invoice"].cast(pl.Utf8)
    canc = inv.str.starts_with("C").mean()
    cust = df["Customer ID"].drop_nulls()
    rep = (df.drop_nulls("Customer ID").group_by("Customer ID").agg(pl.col("Invoice").n_unique().alias("k"))["k"] > 1).mean()
    return [
        P("Cancellations, returns", "secondary", "cancelled / returned lines", "PASS", f"{df.height:,} lines; {pct(canc)} cancellation lines ('C' invoices)"),
        P("Orders, items, payments", "secondary", "invoice lines with quantity and price", "PASS", f"columns {df.columns}; dates {df['InvoiceDate'].min()}..{df['InvoiceDate'].max()}"),
        P("Repeat purchases", "secondary", "customers with several invoices", "PASS", f"{pct(rep)} of {cust.n_unique():,} customers buy more than once"),
    ]


def _mat(path):
    import scipy.io as sio
    m = sio.loadmat(path)
    return {k: v for k, v in m.items() if not k.startswith("__")}


def a_yelpchi(d):
    m = _mat(next(d.dir.rglob("YelpChi.mat")))
    lab = m["label"].ravel()
    keys = {k: getattr(v, "shape", None) for k, v in m.items()}
    return [P("Fake reviews, fraud rings", "primary", "fraud labels + reviewer relations + timing for bursts", "PARTIAL",
              f"{lab.size:,} reviews, {pct(lab.mean())} labelled spam; arrays {keys}; relations R-U-R / R-T-R / R-S-R present, "
              "but NO raw timestamps or text -> burst sizes not measurable from this version")]


def a_amazon_fraud(d):
    m = _mat(next(d.dir.rglob("Amazon.mat")))
    lab = m["label"].ravel()
    keys = {k: getattr(v, "shape", None) for k, v in m.items()}
    return [P("Fake reviews, fraud rings", "secondary", "fraud-labelled users + user relations", "PASS", f"{lab.size:,} users, {pct(lab.mean())} fraud; arrays {keys}")]


def a_coat(d):
    root = next(p for p in d.dir.rglob("train.ascii")).parent
    import numpy as np
    tr = np.loadtxt(root / "train.ascii")
    te = np.loadtxt(root / "test.ascii")
    return [P("Who leaves reviews (MNAR)", "secondary", "self-selected ratings + randomly assigned ratings", "PASS",
              f"train (self-selected) {tr.shape}, {(tr > 0).sum():,} ratings; test (random) {(te > 0).sum():,} ratings; "
              f"mean rating train {tr[tr > 0].mean():.2f} vs random {te[te > 0].mean():.2f}")]


def a_ipinyou(d):
    import bz2
    root = d.dir / "ipinyou.contest.dataset"
    imp = sorted((root / "training2nd").glob("imp.*.txt.bz2"))
    clk = sorted((root / "training2nd").glob("clk.*.txt.bz2"))
    conv = sorted((root / "training2nd").glob("conv.*.txt.bz2"))
    # season 2/3 impression format (contest.ipinyou.com/data.shtml): 24 tab-separated columns;
    # index 17 = AdSlotFloorPrice, 19 = BiddingPrice, 20 = PayingPrice (CPM, RMB fen)
    n = ok = 0
    widths = set()
    pay = []
    with bz2.open(imp[0], "rt", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            widths.add(len(c))
            if len(c) >= 21:
                try:
                    bid, paid, floor = float(c[19]), float(c[20]), float(c[17])
                    ok += paid <= bid
                    pay.append(paid)
                except ValueError:
                    pass
            n += 1
            if n >= 200_000:
                break
    pay.sort()
    return [P("Ads, sponsored lift", "secondary", "auction bid and paying prices, clicks, conversions", "PASS" if ok > 0.99 * len(pay) and pay else "PARTIAL",
              f"genuine contest package (season 1-3 bid/imp/clk/conv logs): season-2 files imp {len(imp)}, clk {len(clk)}, conv {len(conv)}; "
              f"{imp[0].name}: {n:,} lines sampled, column counts {sorted(widths)}, paying <= bidding in {pct(ok / max(len(pay), 1))}, "
              f"median paying price {pay[len(pay) // 2] if pay else '-'} (CPM fen); no treatment randomisation -> auction prices only, not ad lift")]


def a_hillstrom(d):
    c = con()
    f = d.dir / "hillstrom.csv"
    cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{f}')").fetchall()]
    arms = c.execute(f"select segment, count(*), avg(visit), avg(conversion), avg(spend) from read_csv_auto('{f}') group by 1 order by 1").fetchall()
    return [P("Ads, sponsored lift", "secondary", "randomised campaign arms with visit/conversion/spend", "PASS",
              f"columns {cols}; arms {[(a, n, round(v, 4), round(cv, 4)) for a, n, v, cv, _ in arms]}")]


def a_nab_loghub(d):
    nab = list((d.dir / "NAB" / "data").rglob("*.csv"))
    labels = json.loads((d.dir / "NAB" / "labels" / "combined_windows.json").read_text())
    logs = list((d.dir / "loghub").rglob("*_2k.log"))
    return [P("Service telemetry, faults", "secondary", "labelled anomaly windows + system logs", "PASS",
              f"NAB {len(nab)} series, {sum(len(v) for v in labels.values())} labelled windows; LogHub {len(logs)} 2k-line samples {[p.name for p in logs]}")]


def a_astronomy(d):
    src = pathlib.Path((d.dir / "SOURCE.txt").read_text().strip())
    flags = json.loads((src / "src/flagd/demo.flagd.json").read_text())["flags"]
    sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return [P("Service telemetry, faults", "primary", "real services emitting OTel telemetry with injectable faults", "PARTIAL",
              f"source @ {sha[:7]}: {len(flags)} fault flags; telemetry not observed yet (running the stack = decision A2)"),
            P("Experiments, assignments", "secondary", "feature-flag controlled conditions", "PASS", f"flagd file-based flags: {sorted(flags)[:6]}…"),
            P("Devices, channels", "secondary", "device/channel attributes", "PARTIAL", "web + react-native app exist; device mix not generated by the shop (Locust/driver decides)")]


def a_tmall(d):
    c = con()
    root = d.dir / "data_format1"
    log = root / "user_log_format1.csv"
    cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{log}')").fetchall()]
    n, users, sellers, items = one(c, f"select count(*), count(distinct user_id), count(distinct seller_id), count(distinct item_id) from read_csv_auto('{log}')")
    acts = dict(c.execute(f"select action_type, count(*) from read_csv_auto('{log}') group by 1 order by 1").fetchall())
    ts = one(c, f"select min(time_stamp), max(time_stamp), count(distinct time_stamp) from read_csv_auto('{log}')")
    multi = one(c, f"select avg((k>1)::int) from (select item_id, count(distinct seller_id) k from read_csv_auto('{log}') group by 1)")[0]
    lab = one(c, f"select count(*), avg(label) from read_csv_auto('{root}/train_format1.csv')")
    ucols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{root}/user_info_format1.csv')").fetchall()]
    names = {0: "click", 1: "add-to-cart", 2: "purchase", 3: "favourite"}   # per Tianchi dataset description [D]
    return [
        P("Funnel view -> cart -> buy, timing", "secondary", "typed events with timestamps", "PARTIAL",
          f"{n:,} events; action types {[(names.get(k, k), v) for k, v in acts.items()]}; time_stamp is month-day only "
          f"({ts[0]}..{ts[1]}, {ts[2]} distinct days): step probabilities yes, minute-level timing NO; columns {cols}"),
        P("Session length, revisits", "secondary", "sessions or ordered events", "PARTIAL",
          "no session id and only day resolution: user-days can stand in for sessions; order within a day unknown"),
        P("Repeat purchases", "secondary", "repeat-buyer labels", "PASS",
          f"{lab[0]:,} labelled new-buyer/seller pairs, {pct(lab[1])} became repeat buyers (Double 11 2014 cohort); user info {ucols}"),
        P("Sellers, regions, size", "secondary", "(bonus) seller ids on every event", "PASS",
          f"{users:,} users, {sellers:,} sellers, {items:,} items; {pct(multi)} of items appear under >1 seller (no geography)"),
    ]


def a_taobao(d):
    c = con()
    f = next(d.dir.rglob("UserBehavior.csv"))
    # no header; documented columns: user_id, item_id, category_id, behavior_type (pv/buy/cart/fav), timestamp (unix s) [D]
    c.execute(f"""create view tb as select * from read_csv('{f}', header=false,
        columns={{'user_id':'BIGINT','item_id':'BIGINT','category_id':'BIGINT','behavior':'VARCHAR','ts':'BIGINT'}})""")
    n, users = one(c, "select count(*), count(distinct user_id) from tb")
    acts = dict(c.execute("select behavior, count(*) from tb group by 1 order by 2 desc").fetchall())
    span = one(c, "select to_timestamp(quantile_cont(ts, 0.001)), to_timestamp(quantile_cont(ts, 0.999)), avg((ts < 1511539200 or ts >= 1512316800)::int) from tb")
    gaps = one(c, """select quantile_cont(g, [0.5, 0.9]) from (select ts - lag(ts) over (partition by user_id order by ts) g
                     from tb using sample 2%) where g > 0""")[0]
    return [
        P("Funnel view -> cart -> buy, timing", "secondary", "typed events with second-level timestamps", "PASS",
          f"{n:,} events from {users:,} users; {acts}; 99.8% within {span[0]:%Y-%m-%d}..{span[1]:%Y-%m-%d}; {pct(span[2])} outside the documented 9 days"),
        P("Session length, revisits", "secondary", "event gaps to cut sessions", "PASS",
          f"no session id, but second-level timestamps: median gap between a user's events {gaps[0]:.0f}s, p90 {gaps[1]:.0f}s -> sessions by inactivity gap"),
        P("Traffic volume, calendar", "secondary", "hourly traffic pattern", "PARTIAL", "only 9 days: hour-of-day yes, weekly/seasonal no"),
    ]


def a_expedia(d):
    c = con()
    f = next(d.dir.rglob("train.csv"))
    cols = [r[0] for r in c.execute(f"describe select * from read_csv_auto('{f}')").fetchall()]
    n, srch, props, rnd = one(c, f"select count(*), count(distinct srch_id), count(distinct prop_id), avg(random_bool) from read_csv_auto('{f}')")
    curve = c.execute(f"""select position, avg(click_bool), avg(booking_bool), count(*) from read_csv_auto('{f}')
                          where random_bool = 1 and position <= 10 group by 1 order by 1""").fetchall()
    top = curve[0][1]
    rel = [(p, round(ck / top, 2)) for p, ck, _, _ in curve]
    return [
        P("Exposure, position, clicks", "primary", "position + click/booking under randomly ordered results", "PASS" if curve[-1][1] < 0.8 * top else "PARTIAL",
          f"{n:,} result rows, {srch:,} searches, {props:,} hotels; {pct(rnd)} of rows from randomly ordered searches; "
          f"click rate relative to position 1 (random order): {rel}; booking@1 {curve[0][2]:.4f} vs @10 {curve[-1][2]:.4f}"),
        P("Multi-seller offers", "secondary", "(bonus) many sellers competing in one result list", "PASS", f"columns include {[c_ for c_ in cols if c_ in ('price_usd','promotion_flag','prop_starrating','prop_review_score','comp1_rate')]}"),
    ]


def a_ga(d):
    c = con()
    f = d.dir / "train.csv"
    c.execute(f"""create view ga as select json_extract_string(device, '$.deviceCategory') dev, channelGrouping ch,
                  (json_extract_string(totals, '$.transactionRevenue') is not null)::int tx from read_csv_auto('{f}', all_varchar=true)""")
    n = one(c, "select count(*) from ga")[0]
    dev = c.execute("select dev, count(*), avg((tx>0)::int) from ga group by 1 order by 2 desc").fetchall()
    ch = c.execute("select ch, count(*), avg((tx>0)::int) from ga group by 1 order by 2 desc limit 8").fetchall()
    return [P("Devices, channels", "secondary", "sessions with device + marketing channel + purchase outcome", "PASS",
              f"{n:,} sessions; conversion by device {[(a, b, round(c_, 4)) for a, b, c_ in dev]}; by channel {[(a, round(c_, 4)) for a, _, c_ in ch]}")]


def a_statcounter(d):
    import csv
    rows = list(csv.DictReader(open(d.dir / "brazil_device_share_2016_2018.csv")))
    first, last = rows[0], rows[-1]
    return [P("Devices, channels", "primary", "Brazil device share per month (Olist years)", "PARTIAL",
              f"{len(rows)} months; mobile {first['Mobile']}% ({first['Date']}) -> {last['Mobile']}% ({last['Date']}); desktop {first['Desktop']}% -> {last['Desktop']}%. "
              "Brazil-specific (differs from worldwide 55.9%->47.2% mobile and US), but it counts page views on sites using Statcounter, "
              "not shoppers -> a prior for device mix, not a measured shopper share")]


def a_blocked(d):
    return []


DATASETS = [
    Dataset("olist", "Olist Brazilian E-Commerce", "Kaggle", "CC BY-NC-SA 4.0", f_olist, a_olist),
    Dataset("olist_funnel", "Olist Marketing Funnel", "Kaggle", "CC BY-NC-SA 4.0", f_funnel, a_funnel),
    Dataset("retailrocket", "Retailrocket e-commerce", "Kaggle", "CC BY-NC-SA 4.0", f_retailrocket, a_retailrocket),
    Dataset("amazon_m2", "Amazon-M2 (KDD Cup 2023)", "AIcrowd", "AIcrowd challenge terms (not reviewed)", f_m2, a_m2),
    Dataset("esci", "Amazon Shopping Queries (ESCI)", "GitHub amazon-science", "Apache-2.0", f_esci, a_esci),
    Dataset("reviews2023", "Amazon Reviews 2023 (sample categories)", "HF McAuley-Lab", "research use; none declared on HF card", f_reviews, a_reviews),
    Dataset("open_bandit", "Open Bandit Dataset (ZOZOTOWN)", "HF zozonext", "CC BY 4.0", f_open_bandit, a_open_bandit),
    Dataset("criteo", "Criteo Uplift v2.1", "HF criteo", "CC BY-NC-SA 4.0", f_criteo, a_criteo),
    Dataset("bcb", "Banco Central do Brasil SGS", "public API", "public data (terms of use of BCB)", f_bcb, a_bcb),
    Dataset("holidays", "Brazil holidays (reference file)", "generated from rules", "n/a", f_holidays_ref, a_holidays),
    Dataset("public_record", "2018 strike window (reference file)", "Wikipedia / news", "n/a", f_strike_ref, a_public),
    Dataset("m5", "M5 Forecasting (Walmart)", "Kaggle competition", "competition rules", f_m5, a_m5),
    Dataset("instacart", "Instacart Market Basket", "Kaggle copy (competition removed)", "Instacart non-commercial terms (original); mirror", f_instacart, a_instacart),
    Dataset("uci", "UCI Online Retail II", "UCI", "CC BY 4.0", f_uci, a_uci),
    Dataset("yelpchi", "YelpChi (CARE-GNN graph)", "GitHub CARE-GNN", "research use", f_yelpchi, a_yelpchi),
    Dataset("amazon_fraud", "Amazon fraud users (CARE-GNN graph)", "GitHub CARE-GNN", "research use", f_amazon_fraud, a_amazon_fraud),
    Dataset("coat", "Coat (MNAR ratings)", "Cornell", "research use", f_coat, a_coat),
    Dataset("yahoo_r3", "Yahoo! R3", "Webscope", "Webscope agreement", f_yahoo, a_blocked),
    Dataset("ipinyou", "iPinYou RTB (mirror)", "Kaggle mirror", "iPinYou release terms (mirror)", f_ipinyou, a_ipinyou),
    Dataset("hillstrom", "Hillstrom email campaign", "MineThatData", "free for research", f_hillstrom, a_hillstrom),
    Dataset("nab_loghub", "NAB + LogHub samples", "GitHub", "AGPL-3.0 (NAB) / research (LogHub)", f_nab_loghub, a_nab_loghub),
    Dataset("astronomy_shop", "OpenTelemetry Astronomy Shop", "GitHub", "Apache-2.0", f_astronomy, a_astronomy),
    Dataset("tmall", "Tmall IJCAI-15 repeat buyers", "Tianchi #42", "Tianchi research terms", f_tmall, a_tmall),
    Dataset("taobao", "Taobao User Behavior", "Tianchi #649", "Tianchi research terms", f_taobao, a_taobao),
    Dataset("expedia", "Expedia hotel search (randomised order)", "Kaggle competition", "competition rules", f_expedia, a_expedia),
    Dataset("ga", "Google Analytics sessions (Merchandise Store)", "Kaggle competition", "competition rules", f_ga, a_ga),
    Dataset("statcounter", "Statcounter Brazil device share", "gs.statcounter.com", "free with attribution (CC BY-SA, per site) [verify]", f_statcounter, a_statcounter),
    Dataset("keepa", "Keepa price history", "commercial API", "commercial", f_keepa, a_blocked),
]
BY = {d.name: d for d in DATASETS}

# Decision 21 Nov 2026: keep the 18 datasets the stitching actually needs; the rest are dropped from the design.
# Dropped data is moved to ~/data/mkt/archive (reversible), not deleted.
DROPPED = {
    "amazon_m2": "redundant: Taobao covers session length with real timestamps; M2 has no event types/timestamps",
    "tmall": "redundant: repeat purchases covered by Olist + Instacart; carts barely logged",
    "ipinyou": "not needed: auction prices only, no auction mechanism in the build",
    "yahoo_r3": "unavailable (Webscope unreachable); Coat covers review self-selection",
    "keepa": "paid source (rule: drop anything paid)",
}
REFERENCE = {"holidays": "reference file, not a dataset", "public_record": "reference file, not a dataset"}
SUPPORT = {"reviews2023": "category map for the ESCI bridge only (R2: review text comes from Olist)",
           "astronomy_shop": "not a dataset: optional live storefront, on hold (A1-A5)"}


# ----------------------------------------------------------------------------- commands
def state_path(d):
    return OUT / f"{d.name}.json"


def load_state(d):
    p = state_path(d)
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(d, **kw):
    OUT.mkdir(parents=True, exist_ok=True)
    s = load_state(d)
    s.update(kw)
    state_path(d).write_text(json.dumps(s, indent=1, default=str))


def cmd_plan(names):
    for d in [BY[n] for n in names]:
        s = load_state(d)
        tag = ("DROPPED: " + DROPPED[d.name] if d.name in DROPPED else "SUPPORT: " + SUPPORT[d.name] if d.name in SUPPORT
               else "REFERENCE: " + REFERENCE[d.name] if d.name in REFERENCE else "KEEP")
        print(f"{d.name:15} fetch={s.get('fetch_status', '-'):8} audit={s.get('audit_status', '-'):8} {tag}")


def cmd_fetch(names):
    for d in [BY[n] for n in names]:
        if d.name in DROPPED:
            print(f"{d.name:15} skipped (dropped: {DROPPED[d.name]})")
            continue
        t = time.time()
        try:
            origin = d.fetch(d)
            files = {p.relative_to(d.dir).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256(p)}
                     for p in sorted(d.dir.rglob("*")) if p.is_file() and ".git" not in p.parts and p.stat().st_size < 50 * 2**30}
            save_state(d, fetch_status="ok", origin=origin, fetched=dt.datetime.now().isoformat(timespec="seconds"),
                       files=files, bytes=sum(f["bytes"] for f in files.values()), licence=d.licence)
            print(f"{d.name:15} ok       {sum(f['bytes'] for f in files.values()) / 1e9:7.2f} GB  {time.time() - t:6.0f}s  {origin}")
        except Blocked as e:
            save_state(d, fetch_status="blocked", reason=str(e))
            print(f"{d.name:15} BLOCKED  {e}")
        except Exception as e:  # noqa: BLE001
            save_state(d, fetch_status="failed", reason=f"{type(e).__name__}: {e}")
            print(f"{d.name:15} FAILED   {type(e).__name__}: {e}")


def cmd_audit(names):
    for d in [BY[n] for n in names]:
        s = load_state(d)
        if s.get("fetch_status") != "ok":
            save_state(d, audit_status="blocked", claims=[])
            print(f"{d.name:15} audit skipped (not fetched: {s.get('reason', 'no fetch')})")
            continue
        t = time.time()
        try:
            claims = d.audit(d)
            save_state(d, audit_status="done", claims=[c.__dict__ for c in claims], audit_error=None,
                       audited=dt.datetime.now().isoformat(timespec="seconds"))
            print(f"{d.name:15} audited {len(claims)} claims in {time.time() - t:.0f}s: "
                  + ", ".join(f"{c.status}" for c in claims))
        except Exception as e:  # noqa: BLE001
            save_state(d, audit_status="failed", audit_error=f"{type(e).__name__}: {e}", claims=[])
            print(f"{d.name:15} AUDIT FAILED {type(e).__name__}: {e}")


def cmd_report(_):
    rows: dict[str, list] = {}
    lines = ["# Dataset retrieval and audit", "",
             f"*Generated {dt.datetime.now():%Y-%m-%d %H:%M} by `scripts/datasets.py report`. Data under `{RAW}`.*", "",
             "## Datasets", "", "| Dataset | Fetch | Size | Origin / reason | Licence |", "|---|---|---|---|---|"]
    for d in DATASETS:
        s = load_state(d)
        size = f"{s.get('bytes', 0) / 1e9:.2f} GB" if s.get("fetch_status") == "ok" else ""
        status = "**dropped**" if d.name in DROPPED else s.get('fetch_status', '-')
        why = DROPPED.get(d.name) or s.get('origin') or s.get('reason', '')
        lines.append(f"| {d.title} | {status} | {size} | {why} | {d.licence} |")
        if d.name in DROPPED:
            continue
        for c in s.get("claims", []):
            rows.setdefault(c["row"], []).append((d.title, c))
        if s.get("audit_error"):
            rows.setdefault("(audit errors)", []).append((d.title, {"role": "-", "claim": "audit", "status": "FAIL", "evidence": s["audit_error"]}))
    notes = OUT / "NOTES.md"            # hand-written decisions and findings, never overwritten
    if notes.exists():
        lines += ["", notes.read_text().rstrip()]
    lines += ["", "## Claims by data type (stitching row)", ""]
    summary = {}
    for row in sorted(rows):
        lines += [f"### {row}", "", "| Dataset | Role | Claim | Status | Evidence |", "|---|---|---|---|---|"]
        for title, c in sorted(rows[row], key=lambda x: (x[1]["role"] != "primary", x[0])):
            lines.append(f"| {title} | {c['role']} | {c['claim']} | **{c['status']}** | {c['evidence'].replace('|', '/')} |")
            summary.setdefault(c["status"], 0)
            summary[c["status"]] += 1
        lines.append("")
    lines[4:4] = [f"**Claims:** " + ", ".join(f"{k} {v}" for k, v in sorted(summary.items())), ""]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "AUDIT.md").write_text("\n".join(lines))
    (OUT / "audit.json").write_text(json.dumps({d.name: load_state(d) for d in DATASETS}, indent=1, default=str))
    print(OUT / "AUDIT.md", summary)


def main(argv=None):
    a = (argv or sys.argv[1:])
    if not a or a[0] not in ("plan", "fetch", "audit", "report"):
        print(__doc__)
        return 2
    names = a[1:] or [d.name for d in DATASETS]
    unknown = [n for n in names if n not in BY]
    if unknown:
        print(f"unknown: {unknown}; known: {list(BY)}")
        return 2
    {"plan": cmd_plan, "fetch": cmd_fetch, "audit": cmd_audit, "report": cmd_report}[a[0]](names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
