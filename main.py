import os
from typing import Optional, List, Dict, Any
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import db, create_document, get_documents
from bson import ObjectId

app = FastAPI(title="Crypto Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COINGECKO_API = "https://api.coingecko.com/api/v3"
BINANCE_API = "https://api.binance.com/api/v3"

class CreatePortfolio(BaseModel):
    name: str
    address: Optional[str] = None

class AddHolding(BaseModel):
    coin_id: str
    symbol: str
    amount: float

@app.get("/")
def root():
    return {"message": "Crypto Platform Backend Running"}

@app.get("/test")
def test_database():
    resp = {"backend": "✅ Running", "database": "❌ Not Available"}
    try:
        if db is not None:
            resp["database"] = "✅ Available"
            resp["collections"] = db.list_collection_names()
    except Exception as e:
        resp["database"] = f"⚠️ {str(e)[:120]}"
    resp["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    resp["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return resp

# Helper to fetch Binance 24h stats for a symbol (e.g., BTCUSDT)

def fetch_binance_ticker(symbol: str):
    r = requests.get(f"{BINANCE_API}/ticker/24hr", params={"symbol": symbol}, timeout=10)
    if r.status_code != 200:
        return None
    j = r.json()
    return {
        "lastPrice": float(j.get("lastPrice", 0) or 0),
        "priceChangePercent": float(j.get("priceChangePercent", 0) or 0),
        "symbol": j.get("symbol"),
    }

@app.get("/api/markets")
def get_markets(page: int = 1, per_page: int = 20):
    """
    Markets endpoint combining CoinGecko logos/names with Binance prices.
    Fallback to CoinGecko prices if a Binance symbol isn't available.
    """
    try:
        cg_params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        r = requests.get(f"{COINGECKO_API}/coins/markets", params=cg_params, timeout=10)
        r.raise_for_status()
        data = r.json()
        out = []
        for c in data:
            sym = (c.get("symbol") or "").upper()
            binance_symbol = f"{sym}USDT"
            ticker = fetch_binance_ticker(binance_symbol)
            price = ticker["lastPrice"] if ticker else c.get("current_price")
            change24 = ticker["priceChangePercent"] if ticker else c.get("price_change_percentage_24h")
            out.append({
                "id": c.get("id"),
                "symbol": c.get("symbol"),
                "name": c.get("name"),
                "image": c.get("image"),
                "current_price": price,
                "price_change_percentage_24h": change24,
                "binance_symbol": ticker["symbol"] if ticker else None,
            })
        return out
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Provider error: {str(e)}")

@app.get("/api/coin/{coin_id}")
def get_coin(coin_id: str):
    try:
        r = requests.get(f"{COINGECKO_API}/coins/{coin_id}", params={"localization": "false"}, timeout=10)
        r.raise_for_status()
        j = r.json()
        sym = (j.get("symbol") or "").upper()
        ticker = fetch_binance_ticker(f"{sym}USDT")
        return {
            "id": j.get("id"),
            "symbol": j.get("symbol"),
            "name": j.get("name"),
            "image": j.get("image", {}).get("large"),
            "description": j.get("description", {}).get("en"),
            "market_data": {
                "current_price": (ticker["lastPrice"] if ticker else j.get("market_data", {}).get("current_price", {}).get("usd")),
                "price_change_percentage_24h": (ticker["priceChangePercent"] if ticker else j.get("market_data", {}).get("price_change_percentage_24h")),
            },
        }
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Provider error: {str(e)}")

@app.get("/api/coin/{coin_id}/history")
def get_coin_history(coin_id: str, days: int = 7):
    """Historical price series for charts (USD)"""
    try:
        r = requests.get(
            f"{COINGECKO_API}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            timeout=10,
        )
        r.raise_for_status()
        j = r.json()
        prices = j.get("prices", [])  # [ [timestamp, price], ... ]
        return {"prices": prices}
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Provider error: {str(e)}")

# ------------------ Portfolio Endpoints ------------------

@app.post("/api/portfolio")
def create_portfolio(payload: CreatePortfolio):
    data = payload.dict()
    pid = create_document("portfolio", data)
    return {"id": pid, **data}

@app.get("/api/portfolio")
def list_portfolios(limit: int = 50):
    docs = get_documents("portfolio", {}, limit)
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs

@app.get("/api/portfolio/{portfolio_id}")
def get_portfolio(portfolio_id: str):
    try:
        doc = db["portfolio"].find_one({"_id": ObjectId(portfolio_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        doc["_id"] = str(doc["_id"])
        return doc
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid portfolio id")

@app.post("/api/portfolio/{portfolio_id}/holdings")
def add_holding(portfolio_id: str, payload: AddHolding):
    try:
        update = db["portfolio"].update_one(
            {"_id": ObjectId(portfolio_id)},
            {"$push": {"holdings": payload.dict()}},
        )
        if update.modified_count == 0:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        return {"ok": True}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid portfolio id")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
