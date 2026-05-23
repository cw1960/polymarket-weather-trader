"""
HTTP shim around the TypeScript Polymarket CLOB v2 client.
==========================================================

Why this exists
---------------
`py_clob_client_v2` has a bug in its L1 auth handshake that prevents API key
creation for accounts using POLY_1271 (deposit wallet) signatures.  The
official TypeScript client `@polymarket/clob-client-v2` works correctly.

`server.mjs` (in /root/polymarket/ts_executor) runs that TS client as a small
HTTP service on 127.0.0.1:8787.  This module is the Python-side adapter:
it exposes a `ClobHTTPClient` class with the same method surface the
executor code expects (`get_order_book`, `get_market`, `cancel`, …) so the
rest of the pipeline doesn't have to change.

When the upstream Python library is fixed, swap the import in `executor.py`
back to `py_clob_client_v2` and delete this file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


BASE_URL = os.getenv("TS_EXECUTOR_URL", "http://127.0.0.1:8787")
TIMEOUT  = float(os.getenv("TS_EXECUTOR_TIMEOUT", "30"))


# ── SDK-compatible stand-ins ────────────────────────────────────────────────
# These exist so executor.py can keep its `from clob_http import ...`
# style imports unchanged. Field names match the upstream SDK.

class OrderType:
    GTC = "GTC"
    FOK = "FOK"
    FAK = "FAK"
    GTD = "GTD"


BUY  = "BUY"
SELL = "SELL"


@dataclass
class OrderArgs:
    token_id: str
    price:    float
    size:     float
    side:     str = BUY


@dataclass
class PartialCreateOrderOptions:
    tick_size: str  | None = None
    neg_risk:  bool | None = None


# Mirrors clob_types.AssetType / BalanceAllowanceParams from the SDK so
# preflight can keep its existing call sites.
class AssetType:
    COLLATERAL  = "COLLATERAL"
    CONDITIONAL = "CONDITIONAL"


@dataclass
class BalanceAllowanceParams:
    asset_type: str = AssetType.COLLATERAL


# ── Book wrapper ────────────────────────────────────────────────────────────
# Match the shape `_snapshot_book` and `_best_ask` rely on:
#   book.asks[0].price, book.bids[0].price

@dataclass
class _Level:
    price: float
    size:  float


class _Book:
    def __init__(self, raw: dict):
        # CLOB returns asks ascending and bids descending in `asks`/`bids` arrays.
        # Each level is {price, size} as strings.
        self.asks = [_Level(float(x["price"]), float(x["size"]))
                     for x in (raw.get("asks") or [])]
        self.bids = [_Level(float(x["price"]), float(x["size"]))
                     for x in (raw.get("bids") or [])]
        # Order book convention: best ask = lowest, best bid = highest.
        # The Polymarket API returns them already sorted that way, but be defensive:
        self.asks.sort(key=lambda l: l.price)
        self.bids.sort(key=lambda l: l.price, reverse=True)


# ── HTTP errors ─────────────────────────────────────────────────────────────

class CLOBHTTPError(RuntimeError):
    """Raised when the TS executor service returns a non-2xx response."""
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status  = status
        self.message = message


def _request(method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None) -> Any:
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.request(method, url, params=params, json=json_body,
                                timeout=TIMEOUT)
    except requests.RequestException as e:
        raise CLOBHTTPError(0, f"network error: {e}") from e
    try:
        body = resp.json()
    except ValueError:
        body = {"error": resp.text or "no body"}
    if not resp.ok:
        msg = body.get("error") if isinstance(body, dict) else str(body)
        raise CLOBHTTPError(resp.status_code, msg or "unknown error")
    return body


# ── Client ──────────────────────────────────────────────────────────────────

class ClobHTTPClient:
    """
    Drop-in replacement for the subset of py_clob_client_v2.ClobClient that
    executor.py uses. All operations are proxied over HTTP to the local
    Node service that wraps @polymarket/clob-client-v2.
    """

    # — Read-only —
    def get_address(self) -> str:
        return _request("GET", "/health").get("eoa", "")

    def get_market(self, condition_id: str) -> dict:
        return _request("GET", "/market", params={"conditionID": condition_id})

    def get_order_book(self, token_id: str) -> _Book:
        raw = _request("GET", "/book", params={"tokenID": token_id})
        return _Book(raw if isinstance(raw, dict) else {})

    def get_order(self, order_id: str) -> dict:
        return _request("GET", "/order", params={"orderID": order_id})

    def get_balance_allowance(self, params: BalanceAllowanceParams | None = None) -> dict:
        # The TS service uses POLY_1271 + funder under the hood.
        # asset_type is informational here; collateral is what we need.
        return _request("GET", "/balance-allowance")

    # — Writes —
    def create_and_post_order(
        self,
        order_args: OrderArgs,
        options:    PartialCreateOrderOptions | None = None,
        order_type: str = OrderType.GTC,
    ) -> dict:
        body = {
            "tokenID":   order_args.token_id,
            "price":     float(order_args.price),
            "size":      float(order_args.size),
            "side":      order_args.side,
            "orderType": order_type,
        }
        if options is not None:
            if options.tick_size is not None: body["tickSize"] = options.tick_size
            if options.neg_risk  is not None: body["negRisk"]  = bool(options.neg_risk)
        return _request("POST", "/place-order", json_body=body)

    def cancel(self, order_id: str) -> dict:
        return _request("POST", "/cancel-order", json_body={"orderID": order_id})


# ── Module-level singleton ─────────────────────────────────────────────────

_client: ClobHTTPClient | None = None


def get_client() -> ClobHTTPClient:
    """Return the process-wide ClobHTTPClient instance."""
    global _client
    if _client is None:
        _client = ClobHTTPClient()
    return _client
