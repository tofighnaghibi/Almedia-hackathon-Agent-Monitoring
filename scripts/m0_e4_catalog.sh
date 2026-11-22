#!/usr/bin/env bash
# E4: catalog scale. Adds N synthetic rows (ids E4-*) to the M0 stack's catalog.products, times ListProducts
# through the frontend and the SearchProducts SQL (unindexed LIKE) in the database, then removes the E4 rows.
set -euo pipefail
PSQL="docker exec -i astronomy-db psql -U postgres -d astronomy_db -qtA"
for N in 0 1000 10000; do
  $PSQL -c "delete from catalog.products where id like 'E4-%'"
  if [ "$N" -gt 0 ]; then
    $PSQL -c "insert into catalog.products select 'E4-'||g, 'Synthetic product '||g||' category '||(g%70),
              'Bridged description number '||g||' for scale testing', 'Telescope.jpg', 'USD', 10+(g%500), 0, 'telescopes'
              from generate_series(1,$N) g"
  fi
  rows=$($PSQL -c "select count(*) from catalog.products")
  t=()
  for i in $(seq 1 10); do t+=($(curl -s -o /dev/null -w "%{time_total}" "http://localhost:8080/api/products?currencyCode=USD")); done
  list=$(printf "%s\n" "${t[@]}" | sort -n | sed -n 5p)
  eur=$(curl -s -o /dev/null -w "%{time_total}" "http://localhost:8080/api/products?currencyCode=EUR")
  search=$($PSQL -c "explain (analyze, format json) select * from catalog.products p where lower(p.name) like '%telescope%' or lower(p.description) like '%telescope%' order by p.id" | python3 -c "import json,sys;print(round(json.load(sys.stdin)[0]['Execution Time'],2))")
  echo "{\"rows\": $rows, \"list_products_median_s\": $list, \"list_products_eur_s\": $eur, \"search_sql_ms\": $search}"
done
$PSQL -c "delete from catalog.products where id like 'E4-%'"
