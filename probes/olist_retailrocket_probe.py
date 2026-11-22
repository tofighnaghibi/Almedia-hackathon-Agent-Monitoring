"""Measure the Olist / Olist-funnel / Retailrocket facts used in docs/ARCHITECTURE.md.

    kaggle datasets download olistbr/brazilian-ecommerce   -p $DATA/olist --unzip
    kaggle datasets download olistbr/marketing-funnel-olist -p $DATA/olist/funnel --unzip
    kaggle datasets download retailrocket/ecommerce-dataset -p $DATA/retailrocket --unzip
    python -I probes/olist_retailrocket_probe.py
"""
import os
import pathlib

import duckdb

DATA = pathlib.Path(os.environ.get("DATA", "~/.cache/mkt-probe/data")).expanduser()
O, RR = DATA / "olist", DATA / "retailrocket"
c = duckdb.connect()
for t in ["customers", "geolocation", "order_items", "order_payments", "order_reviews",
          "orders", "products", "sellers"]:
    c.sql(f"create view {t} as select * from read_csv_auto('{O}/olist_{t}_dataset.csv')")
c.sql(f"create view deals as select * from read_csv_auto('{O}/funnel/olist_closed_deals_dataset.csv')")
c.sql(f"create view mql as select * from read_csv_auto('{O}/funnel/olist_marketing_qualified_leads_dataset.csv')")


def q(title, sql):
    print(f"\n## {title}")
    print(c.sql(sql))


q("row counts", """select (select count(*) from orders) orders, (select count(*) from order_items) items,
  (select count(*) from order_payments) payments, (select count(*) from order_reviews) reviews,
  (select count(*) from customers) customers, (select count(distinct customer_unique_id) from customers) unique_buyers,
  (select count(*) from sellers) sellers, (select count(*) from products) products""")
q("order time span and status", """select order_status, count(*) n, min(order_purchase_timestamp) first, max(order_purchase_timestamp) last
  from orders group by 1 order by 2 desc""")
q("orders per month (coverage / truncation)", """select strftime(order_purchase_timestamp,'%Y-%m') m, count(*) n
  from orders group by 1 order by 1""")
q("FK orphans", """select
  (select count(*) from order_items i anti join orders o using(order_id)) items_wo_order,
  (select count(*) from order_items i anti join sellers s using(seller_id)) items_wo_seller,
  (select count(*) from order_items i anti join products p using(product_id)) items_wo_product,
  (select count(*) from order_reviews r anti join orders o using(order_id)) reviews_wo_order,
  (select count(*) from order_payments p anti join orders o using(order_id)) payments_wo_order,
  (select count(*) from orders o anti join customers c using(customer_id)) orders_wo_customer,
  (select count(*) from orders o anti join order_items i using(order_id)) orders_wo_items,
  (select count(*) from orders o anti join order_payments p using(order_id)) orders_wo_payment""")
q("customer_id is per order; repeat buyers by customer_unique_id", """
  with b as (select c.customer_unique_id u, count(distinct o.order_id) n
             from orders o join customers c using(customer_id) group by 1)
  select count(*) buyers, sum((n=1)::int) one_order, round(100.0*sum((n=1)::int)/count(*),2) pct_one_order,
         max(n) max_orders,
         (select count(*)=count(distinct customer_id) from customers) customer_id_unique_per_row
  from b""")
q("reviews: duplicates and coverage", """select count(*) rows_, count(distinct review_id) review_ids,
  count(distinct order_id) orders_reviewed,
  (select count(*) from (select order_id from order_reviews group by 1 having count(*)>1)) orders_multi_review,
  (select count(*) from (select review_id from order_reviews group by 1 having count(distinct order_id)>1)) review_ids_multi_order,
  round(100.0*count(review_comment_message)/count(*),1) pct_with_text,
  (select count(*) from orders o anti join order_reviews r using(order_id)) orders_without_review
  from order_reviews""")
q("review score distribution", "select review_score, count(*) n, round(100.0*count(*)/sum(count(*)) over(),1) pct from order_reviews group by 1 order by 1")
q("review created vs delivery (is review before delivery possible?)", """select
  count(*) delivered_reviewed,
  sum((r.review_creation_date < o.order_delivered_customer_date)::int) review_before_delivery,
  round(100.0*sum((r.review_creation_date < o.order_delivered_customer_date)::int)/count(*),1) pct
  from order_reviews r join orders o using(order_id) where o.order_delivered_customer_date is not null""")
q("timestamp anomalies", """select
  sum((order_approved_at < order_purchase_timestamp)::int) approved_before_purchase,
  sum((order_delivered_carrier_date < order_purchase_timestamp)::int) carrier_before_purchase,
  sum((order_delivered_carrier_date < order_approved_at)::int) carrier_before_approval,
  sum((order_delivered_customer_date < order_delivered_carrier_date)::int) delivered_before_carrier,
  sum((order_status='delivered' and order_delivered_customer_date is null)::int) delivered_status_no_date,
  sum((order_status<>'delivered' and order_delivered_customer_date is not null)::int) date_but_not_delivered_status
  from orders""")
q("competition base: products sold by >1 seller", """
  with p as (select product_id, count(distinct seller_id) s, count(*) items from order_items group by 1)
  select count(*) products_sold, sum((s>1)::int) multi_seller_products, round(100.0*sum((s>1)::int)/count(*),2) pct,
         sum(case when s>1 then items end) items_in_multi_seller, round(100.0*sum(case when s>1 then items end)/sum(items),2) pct_items,
         max(s) max_sellers
  from p""")
q("orders with >1 seller (split fulfilment)", """select count(*) orders, sum((s>1)::int) multi_seller_orders
  from (select order_id, count(distinct seller_id) s from order_items group by 1)""")
q("seller size distribution (items sold)", """select quantile_cont(n,[0.5,0.9,0.99]) q, max(n), count(*) sellers_with_sales
  from (select seller_id, count(*) n from order_items group by 1)""")
q("money reconciliation: items+freight vs payments", """
  with i as (select order_id, sum(price+freight_value) due from order_items group by 1),
       p as (select order_id, sum(payment_value) paid from order_payments group by 1)
  select count(*) orders, sum((abs(due-paid)<=0.01)::int) exact, sum((abs(due-paid)<=1)::int) within_1brl,
         round(100.0*sum((abs(due-paid)<=1)::int)/count(*),2) pct_within_1brl,
         sum((paid-due>1)::int) overpaid, sum((due-paid>1)::int) underpaid
  from i join p using(order_id)""")
q("payment types / vouchers", "select payment_type, count(*) n, round(avg(payment_installments),2) avg_inst from order_payments group by 1 order by 2 desc")
q("delivery: actual vs estimate and its link to review score (M-08/M-09)", """
  with d as (select o.order_id, date_diff('day', order_purchase_timestamp, order_delivered_customer_date) d_days,
                    date_diff('day', order_estimated_delivery_date, order_delivered_customer_date) late_days
             from orders o where order_delivered_customer_date is not null)
  select case when late_days<=0 then 'on time' when late_days<=3 then 'late 1-3d' when late_days<=10 then 'late 4-10d' else 'late >10d' end bucket,
         count(*) orders, round(avg(d_days),1) avg_days, round(avg(r.review_score),2) avg_score,
         round(100.0*avg((r.review_score<=2)::int),1) pct_score_1_2
  from d join order_reviews r using(order_id) group by 1 order by min(late_days)""")
q("Black Friday 2017 (spike check)", """with d as (select cast(order_purchase_timestamp as date) dt, count(*) n from orders
  where order_purchase_timestamp between '2017-10-15' and '2017-12-15' group by 1)
  select dt, n, round(n/(select median(n) from d),2) x_median from d order by n desc limit 5""")
q("2018 truckers' strike (21 May-1 Jun): late share by purchase week", """
  select date_trunc('week', order_purchase_timestamp)::date wk, count(*) orders,
         round(100.0*avg((order_delivered_customer_date>order_estimated_delivery_date)::int),1) pct_late,
         round(avg(date_diff('day',order_purchase_timestamp,order_delivered_customer_date)),1) avg_days
  from orders where order_delivered_customer_date is not null
    and order_purchase_timestamp between '2018-04-01' and '2018-07-15' group by 1 order by 1""")
q("marketing funnel joins on seller_id", """select (select count(*) from mql) mqls, (select count(*) from deals) deals,
  (select count(distinct seller_id) from deals) deal_sellers,
  (select count(distinct d.seller_id) from deals d semi join order_items i using(seller_id)) deal_sellers_with_sales,
  (select min(won_date) from deals) first_won, (select max(won_date) from deals) last_won""")

# ---------------- Retailrocket ----------------
c.sql(f"create view ev as select * from read_csv_auto('{RR}/events.csv')")
q("Retailrocket events", """select event, count(*) n, count(distinct visitorid) visitors, count(distinct itemid) items,
  to_timestamp(min(timestamp)/1000) first, to_timestamp(max(timestamp)/1000) last from ev group by 1""")
q("Retailrocket: transactions with a prior view / cart of the same item by the same visitor", """
  with t as (select visitorid, itemid, min(timestamp) ts from ev where event='transaction' group by 1,2)
  select count(*) txn_pairs,
    sum((exists(select 1 from ev v where v.visitorid=t.visitorid and v.itemid=t.itemid and v.event='view' and v.timestamp<=t.ts))::int) with_prior_view,
    sum((exists(select 1 from ev v where v.visitorid=t.visitorid and v.itemid=t.itemid and v.event='addtocart' and v.timestamp<=t.ts))::int) with_prior_cart
  from t""")
q("Retailrocket: visitor concentration (bot signal)", """select quantile_cont(n,[0.5,0.99,0.9999]) q, max(n) max_events
  from (select visitorid, count(*) n from ev group by 1)""")
c.sql(f"""create view ip as select * from read_csv_auto(['{RR}/item_properties_part1.csv','{RR}/item_properties_part2.csv'])""")
q("Retailrocket: time-varying item properties (availability?)", """select property, count(*) n, count(distinct itemid) items,
  count(distinct value) vals from ip where property in ('available','categoryid','790','888') group by 1""")
