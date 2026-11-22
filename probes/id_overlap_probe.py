"""Reproduce the product-ID overlap measurements in docs/ARCHITECTURE.md §4.

Disposable and read-only: downloads to DATA (default ~/.cache/mkt-probe/data),
never touches any repository.

    uv venv .venv && uv pip install duckdb
    python -I probes/id_overlap_probe.py

Downloads (≈1.8 GB total):
  * ESCI product_id/product_locale, projected from the tasksource/esci HF mirror
    (only two columns are read, ~108 s on the original run)
  * Amazon Reviews 2023 asin2category.json (1.25 GB; keyed by parent_asin, see §4)
  * Amazon Reviews 2023 All_Beauty reviews + metadata (0.54 GB) to test the
    parent-vs-child ASIN question
"""
import os
import pathlib
import urllib.request

import duckdb

DATA = pathlib.Path(os.environ.get("DATA", "~/.cache/mkt-probe/data")).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
HF = "https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main/"


def fetch(rel: str) -> pathlib.Path:
    out = DATA / pathlib.Path(rel).name
    if not out.exists():
        urllib.request.urlretrieve(HF + rel, out)
    return out


c = duckdb.connect()

# 1. ESCI ids (remote column projection, no full download)
esci = DATA / "esci_ids.parquet"
if not esci.exists():
    c.sql(f"""copy (select distinct product_id, product_locale
                    from 'hf://datasets/tasksource/esci/data/*.parquet') to '{esci}'""")

# 2. Reviews 2023 id map -> TSV (streamed; avoids holding a 35M-key dict)
tsv = DATA / "rev_ids.tsv"
if not tsv.exists():
    import mmap, re
    src = fetch("asin2category.json")
    pat = re.compile(rb'"([^"]+)": "([^"]*)"')   # flat {"ASIN": "Category", ...}
    with open(src, "rb") as f, open(tsv, "wb") as out, \
         mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        for m in pat.finditer(mm):                 # streams; no 35M-key dict in RAM
            out.write(m.group(1) + b"\t" + m.group(2) + b"\n")

c.sql(f"""create table r as select * from read_csv('{tsv}', delim='\t', header=false,
          columns={{'asin':'VARCHAR','cat':'VARCHAR'}}, quote='')""")
c.sql(f"create table e as select * from '{esci}'")
print(c.sql("select count(*) ids, count(distinct asin) from r"))
print(c.sql("""select e.product_locale, count(*) esci_ids, count(r.asin) in_reviews2023,
                      round(100.0*count(r.asin)/count(*),1) pct
               from e left join r on e.product_id = r.asin group by 1 order by 1"""))
print(c.sql("select count(*) ids_in_multiple_locales from (select product_id from e group by 1 having count(*)>1)"))

# 3. Is the map keyed by parent_asin or child asin? (All_Beauty sample)
rv = fetch("raw/review_categories/All_Beauty.jsonl")
c.sql(f"create table rv as select asin, parent_asin, user_id, timestamp from read_json_auto('{rv}')")
print(c.sql("""select
  (select count(*) from (select distinct parent_asin a from rv) x join r on x.a=r.asin) parents_in_map,
  (select count(*) from (select distinct asin a from rv where asin<>parent_asin) x join r on x.a=r.asin) child_only_in_map"""))
print(c.sql("""select
  (select count(distinct parent_asin) from rv where parent_asin in (select product_id from e where product_locale='us')) parent_hits,
  (select count(distinct asin) from rv where asin<>parent_asin
      and asin in (select product_id from e where product_locale='us')
      and parent_asin not in (select product_id from e where product_locale='us')) child_only_hits"""))

# 4. Amazon-M2: the official AIcrowd release (website download) and the Kaggle mirror
#    marquis03/amazon-m2 are byte-identical (sha256 f4de2243… products, 83f9d5c7… sessions).
#    3,606,249 sessions, 1,410,675 distinct product ids
#    kaggle datasets download marquis03/amazon-m2 -p $DATA/m2 --unzip
m2 = DATA / "m2" / "products_train.csv"
if m2.exists():
    c.sql(f"""create table p as select id, locale from read_csv('{m2}', header=true, parallel=false,
              columns={{'id':'VARCHAR','locale':'VARCHAR','title':'VARCHAR','price':'DOUBLE','brand':'VARCHAR',
              'color':'VARCHAR','size':'VARCHAR','model':'VARCHAR','material':'VARCHAR','author':'VARCHAR','desc':'VARCHAR'}})""")
    print(c.sql("select count(*) rows_, count(distinct id) ids from p"))
    print(c.sql("""select p.locale, count(*) m2, count(r.asin) in_reviews23, round(100.0*count(r.asin)/count(*),1) pct
                   from p left join r on p.id=r.asin group by 1 order by 1"""))
    print(c.sql("""select p.locale, e.product_locale esci_locale, count(*) shared
                   from p join e on p.id=e.product_id group by 1,2 order by 3 desc limit 10"""))
