#!/usr/bin/env bash
# Load the demo slice into PostgreSQL (local or Supabase).
#
#   DATABASE_URL='postgresql://user:password@host:5432/dbname' \
#       bash data_prep/demo_slice/load_demo_slice.sh
#
# Supabase: Project Settings -> Database -> Connection string (URI). Use the
# "Session pooler" string if your network has no IPv6.
#
# Steps: create every table from backend/schema.sql (run once on an empty
# database), then \copy each CSV in foreign-key order, then report row counts
# and the database size. Requires the psql client (PostgreSQL 13+ server).
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL to the target database connection string}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SLICE_DIR="${SLICE_DIR:-$REPO_ROOT/data_prep/processed/demo_slice}"
SCHEMA="$REPO_ROOT/backend/schema.sql"

# Parents before children so every foreign key resolves on insert.
TABLES=(stores customers products orders order_items events
        marketing_campaigns message_sends coupon_campaigns coupon_redemptions
        holidays causal_data)

PSQL=(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q)

if [[ "${SKIP_SCHEMA:-0}" != "1" ]]; then
  echo "Creating tables from backend/schema.sql ..."
  "${PSQL[@]}" -f "$SCHEMA"
fi

for table in "${TABLES[@]}"; do
  csv="$SLICE_DIR/$table.csv"
  if [[ ! -f "$csv" ]]; then
    echo "  skip $table (no $csv)"
    continue
  fi
  columns="$(head -n 1 "$csv")"
  echo "  loading $table ..."
  "${PSQL[@]}" -c "\\copy $table ($columns) FROM '$csv' WITH (FORMAT csv, HEADER true, NULL '')"
done

echo
echo "Row counts:"
for table in "${TABLES[@]}"; do
  printf '  %-22s %s\n' "$table" "$("${PSQL[@]}" -At -c "SELECT count(*) FROM $table")"
done
echo
echo "Database size: $("${PSQL[@]}" -At -c "SELECT pg_size_pretty(pg_database_size(current_database()))")"
