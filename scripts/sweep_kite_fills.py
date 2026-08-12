"""Reconcile filled Kite orders into portfolios for every user with a
live token — the safety net for fills that land while nobody has the
app open (Kite only exposes TODAY's orders, so anything missed before
the token expires would otherwise be lost).

Run by cron every 10 minutes during market hours; a user's expired
token 403s, clears itself, and the user is skipped until they
reconnect.
"""
from backend import db, fills, kite_connect, queries


def main() -> None:
    with db._conn() as conn:
        user_ids = [r["user_id"] for r in conn.execute(
            "SELECT user_id FROM kite_sessions").fetchall()]
    if not user_ids:
        return
    bonds = queries.symbol_bond_map()
    for user_id in user_ids:
        try:
            orders = kite_connect.list_orders(user_id)
        except kite_connect.KiteError:
            continue  # expired/broken session; token already cleared on 403
        for o in orders:
            hit = bonds.get(((o.get("exchange") or "").upper(),
                             o.get("tradingsymbol") or ""))
            o["isin"] = hit["isin"] if hit else None
            o["issuer_name"] = hit["issuer_name"] if hit else None
        created = fills.process_orders(user_id, orders)
        if created:
            print(f"user {user_id}: {created} lot(s) created from fills")


if __name__ == "__main__":
    try:
        main()
    finally:
        db.close_pool()
