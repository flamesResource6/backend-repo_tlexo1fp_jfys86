from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import requests
import os
from bson import ObjectId

from database import db, create_document
from schemas import Portfolio, Holding

BINANCE_BASE = "https://api.binance.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

app = FastAPI(title="Neo Exchange API", version="1.0.0")

# CORS for frontend preview
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------- Utilities ---------

def to_object_id(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def coingecko_markets(page: int, per_page: int) -> List[Dict[str, Any]]:
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": per_page,
        "page": page,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    r = requests.get(f"{COINGECKO_BASE}/coins/markets", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def binance_24h(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def coingecko_price(coin_ids: List[str]) -> Dict[str, float]:
    if not coin_ids:
        return {}
    r = requests.get(
        f"{COINGECKO_BASE}/simple/price",
        params={"ids": ",".join(coin_ids), "vs_currencies": "usd"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return {k: float(v.get("usd", 0)) for k, v in data.items()}


# --------- Health ---------

@app.get("/test")
def test():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


# --------- Markets ---------

@app.get("/api/markets")
def get_markets(page: int = Query(1, ge=1), per_page: int = Query(30, ge=1, le=250)):
    try:
        cg = coingecko_markets(page, per_page)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CoinGecko error: {e}")

    results = []
    for coin in cg:
        symbol_uc = str(coin.get("symbol", "")).upper()
        binance_symbol = f"{symbol_uc}USDT" if symbol_uc else None
        price = coin.get("current_price")
        change = coin.get("price_change_percentage_24h")

        if binance_symbol:
            b = binance_24h(binance_symbol)
            if b and "lastPrice" in b:
                try:
                    price = float(b.get("lastPrice"))
                    open_price = float(b.get("openPrice", 0))
                    if open_price:
                        change = ((price - open_price) / open_price) * 100.0
                except Exception:
                    pass

        results.append({
            "id": coin.get("id"),
            "symbol": coin.get("symbol"),
            "name": coin.get("name"),
            "image": coin.get("image"),
            "current_price": price,
            "price_change_percentage_24h": change,
            "binance_symbol": binance_symbol,
        })

    return results


@app.get("/api/coin/{coin_id}")
def get_coin(coin_id: str):
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/{coin_id}", params={"localization": "false"}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CoinGecko error: {e}")

    # Try Binance price override
    symbol_uc = data.get("symbol", "").upper()
    price = None
    change = None
    if symbol_uc:
        b = binance_24h(f"{symbol_uc}USDT")
        if b and "lastPrice" in b:
            try:
                price = float(b.get("lastPrice"))
                open_price = float(b.get("openPrice", 0))
                if open_price:
                    change = ((price - open_price) / open_price) * 100.0
            except Exception:
                pass

    if price is None:
        # fallback to CoinGecko market data
        try:
            prices = coingecko_price([coin_id])
            price = prices.get(coin_id)
        except Exception:
            price = None

    return {
        "id": data.get("id"),
        "symbol": data.get("symbol"),
        "name": data.get("name"),
        "image": data.get("image", {}).get("small"),
        "market_data": {
            "current_price": price,
            "price_change_percentage_24h": change,
        },
        "links": data.get("links", {}),
        "description": data.get("description", {}).get("en", ""),
    }


@app.get("/api/coin/{coin_id}/history")
def coin_history(coin_id: str, days: int = Query(7, ge=1, le=365)):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return {"prices": data.get("prices", [])}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CoinGecko error: {e}")


# --------- Portfolio ---------

class PortfolioIn(BaseModel):
    name: str
    address: Optional[str] = None


@app.post("/api/portfolio")
def create_portfolio(p: PortfolioIn):
    portfolio = Portfolio(name=p.name, address=p.address)
    inserted_id = create_document("portfolio", portfolio)
    return {"id": inserted_id, "name": portfolio.name, "address": portfolio.address}


@app.get("/api/portfolio")
def list_portfolios():
    docs = db["portfolio"].find().sort("created_at", -1)
    res = []
    for d in docs:
        res.append({
            "id": str(d.get("_id")),
            "name": d.get("name"),
            "address": d.get("address"),
        })
    return res


@app.get("/api/portfolio/{pid}")
def get_portfolio(pid: str):
    doc = db["portfolio"].find_one({"_id": to_object_id(pid)})
    if not doc:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    doc["id"] = str(doc.pop("_id"))
    return doc


class HoldingIn(BaseModel):
    coin_id: str
    symbol: str
    amount: float


@app.post("/api/portfolio/{pid}/holdings")
def add_holding(pid: str, h: HoldingIn):
    doc = db["portfolio"].find_one({"_id": to_object_id(pid)})
    if not doc:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    holding = Holding(coin_id=h.coin_id, symbol=h.symbol, amount=h.amount).model_dump()
    holding["created_at"] = datetime.now(timezone.utc)

    db["portfolio"].update_one(
        {"_id": to_object_id(pid)},
        {"$push": {"holdings": holding}, "$set": {"updated_at": datetime.now(timezone.utc)}},
    )
    return {"ok": True}


class TxIn(BaseModel):
    type: str  # deposit | withdrawal
    coin_id: str
    symbol: str
    amount: float
    tx_hash: Optional[str] = None


@app.post("/api/portfolio/{pid}/transactions")
def add_transaction(pid: str, tx: TxIn):
    if tx.type not in ("deposit", "withdrawal"):
        raise HTTPException(status_code=400, detail="Invalid transaction type")
    doc = db["portfolio"].find_one({"_id": to_object_id(pid)})
    if not doc:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    tx_doc = tx.model_dump()
    tx_doc["timestamp"] = datetime.now(timezone.utc)
    db["portfolio"].update_one(
        {"_id": to_object_id(pid)},
        {"$push": {"transactions": tx_doc}, "$set": {"updated_at": datetime.now(timezone.utc)}},
    )
    return {"ok": True}


@app.get("/api/portfolio/{pid}/summary")
def portfolio_summary(pid: str):
    doc = db["portfolio"].find_one({"_id": to_object_id(pid)})
    if not doc:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    holdings = doc.get("holdings", [])
    coin_ids = list({h.get("coin_id") for h in holdings if h.get("coin_id")})
    prices = coingecko_price(coin_ids)

    items = []
    total_value = 0.0
    for h in holdings:
        cid = h.get("coin_id")
        sym = h.get("symbol")
        amt = float(h.get("amount", 0))
        price = float(prices.get(cid, 0))
        value = amt * price
        total_value += value
        items.append({
            "coin_id": cid,
            "symbol": sym,
            "amount": amt,
            "price": price,
            "value": value,
        })

    txs = doc.get("transactions", []) or []
    txs_sorted = sorted(
        txs,
        key=lambda x: x.get("timestamp", datetime.now(timezone.utc)),
        reverse=True,
    )[:20]

    return {
        "total_value": total_value,
        "holdings": items,
        "transactions": [
            {
                "type": t.get("type"),
                "coin_id": t.get("coin_id"),
                "symbol": t.get("symbol"),
                "amount": t.get("amount"),
                "tx_hash": t.get("tx_hash"),
                "timestamp": t.get("timestamp"),
            }
            for t in txs_sorted
        ],
    }
