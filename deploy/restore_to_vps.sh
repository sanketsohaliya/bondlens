#!/usr/bin/env bash
# Pipe a fresh dump of the local dockerized DB into the VPS DB.
# Usage: deploy/restore_to_vps.sh [ssh-host]   (default: bondlens-vps)
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:-bondlens-vps}"

echo "local db size:"
docker compose exec -T db psql -U bondlens -tAc \
  "select pg_size_pretty(pg_database_size(current_database()))"

echo "dumping + restoring to $HOST ..."
docker compose exec -T db pg_dump -U bondlens --clean --if-exists bondlens \
  | gzip \
  | ssh "$HOST" 'cd /opt/bondlens && gunzip | docker compose exec -T db psql -q -U bondlens bondlens' \
  | tail -5

echo "row counts on $HOST:"
ssh "$HOST" 'cd /opt/bondlens && docker compose exec -T db psql -U bondlens -tAc "select (select count(*) from bonds) as bonds, (select count(*) from bond_cashflows) as cashflows"'
