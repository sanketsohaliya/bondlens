"""Watchlist API unit tests. The db layer is faked in-memory (CI has no
Postgres); these cover routing, validation and WatchlistError -> HTTP
mapping. The SQL itself is exercised by the e2e suite / production."""
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import auth, db
from backend.app import app


class FakeStore:
    """In-memory stand-in for the user_watchlists tables."""

    def __init__(self):
        self.next_id = 1
        self.lists: dict[int, dict[str, Any]] = {}  # id -> {user_id,name,isins}
        self.holdings: dict[int, list[str]] = {}

    # --- db-compatible functions -------------------------------------
    def get_user_lists(self, user_id: int) -> dict[str, Any]:
        return {
            "holdings": self.holdings.get(user_id, []),
            "watchlists": [
                {"id": i, "name": w["name"], "isins": w["isins"]}
                for i, w in self.lists.items() if w["user_id"] == user_id
            ],
        }

    def create_watchlist(self, user_id: int, name: str) -> dict[str, Any]:
        name = name.strip()
        if not 1 <= len(name) <= 40:
            raise db.WatchlistError(400, "List name must be 1-40 characters")
        mine = [w for w in self.lists.values() if w["user_id"] == user_id]
        if len(mine) >= db.MAX_WATCHLISTS:
            raise db.WatchlistError(400, f"At most {db.MAX_WATCHLISTS} watchlists")
        if any(w["name"] == name for w in mine):
            raise db.WatchlistError(409, f"A list named '{name}' already exists")
        wid = self.next_id
        self.next_id += 1
        self.lists[wid] = {"user_id": user_id, "name": name, "isins": []}
        return {"id": wid, "name": name, "isins": []}

    def _owned(self, user_id: int, wid: int) -> dict[str, Any]:
        w = self.lists.get(wid)
        if w is None or w["user_id"] != user_id:
            raise db.WatchlistError(404, "Watchlist not found")
        return w

    def rename_watchlist(self, user_id: int, wid: int, name: str) -> None:
        name = name.strip()
        if not 1 <= len(name) <= 40:
            raise db.WatchlistError(400, "List name must be 1-40 characters")
        self._owned(user_id, wid)["name"] = name

    def delete_watchlist(self, user_id: int, wid: int) -> None:
        self._owned(user_id, wid)
        del self.lists[wid]

    def add_watchlist_item(self, user_id: int, wid: int, isin: str) -> None:
        w = self._owned(user_id, wid)
        if isin not in w["isins"]:
            w["isins"].append(isin)

    def remove_watchlist_item(self, user_id: int, wid: int, isin: str) -> None:
        w = self._owned(user_id, wid)
        if isin in w["isins"]:
            w["isins"].remove(isin)


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    for fn in ("get_user_lists", "create_watchlist", "rename_watchlist",
               "delete_watchlist", "add_watchlist_item",
               "remove_watchlist_item"):
        monkeypatch.setattr(db, fn, getattr(store, fn))
    app.dependency_overrides[auth.require_user] = lambda: 1
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_crud_flow(client):
    # create
    r = client.post("/api/watchlists", json={"name": "PSU Bonds"})
    assert r.status_code == 200
    wid = r.json()["id"]
    assert r.json() == {"id": wid, "name": "PSU Bonds", "isins": []}

    # add a bond (isin normalized to upper case)
    r = client.put(f"/api/watchlists/{wid}/ine002a08ab1")
    assert r.status_code == 200
    assert r.json()["present"] is True

    # shows up in GET /api/lists
    r = client.get("/api/lists")
    assert r.status_code == 200
    assert r.json() == {
        "holdings": [],
        "watchlists": [
            {"id": wid, "name": "PSU Bonds", "isins": ["INE002A08AB1"]}
        ],
    }

    # rename
    r = client.patch(f"/api/watchlists/{wid}", json={"name": "PSU 2026"})
    assert r.status_code == 200
    assert client.get("/api/lists").json()["watchlists"][0]["name"] == "PSU 2026"

    # remove the bond, then the list
    assert client.delete(f"/api/watchlists/{wid}/INE002A08AB1").status_code == 200
    assert client.delete(f"/api/watchlists/{wid}").status_code == 200
    assert client.get("/api/lists").json()["watchlists"] == []


def test_duplicate_name_conflicts(client):
    assert client.post("/api/watchlists", json={"name": "A"}).status_code == 200
    assert client.post("/api/watchlists", json={"name": "A"}).status_code == 409


def test_bad_names_rejected(client):
    assert client.post("/api/watchlists", json={"name": "  "}).status_code == 400
    assert client.post("/api/watchlists",
                       json={"name": "x" * 41}).status_code == 400


def test_unknown_list_is_404(client):
    assert client.patch("/api/watchlists/999", json={"name": "B"}).status_code == 404
    assert client.delete("/api/watchlists/999").status_code == 404
    assert client.put("/api/watchlists/999/INE002A08AB1").status_code == 404


def test_list_cap(client):
    for i in range(db.MAX_WATCHLISTS):
        assert client.post("/api/watchlists",
                           json={"name": f"L{i}"}).status_code == 200
    assert client.post("/api/watchlists", json={"name": "over"}).status_code == 400


def test_old_watchlist_route_gone(client):
    # only holdings survives on the legacy /api/lists/{name} routes
    assert client.put("/api/lists/watchlist/INE002A08AB1").status_code == 422
