"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- Portfolio -> "portfolio"
- Holding -> embedded in portfolio.holdings
"""

from pydantic import BaseModel, Field
from typing import Optional, List

class Holding(BaseModel):
    coin_id: str = Field(..., description="CoinGecko coin id, e.g., 'bitcoin'")
    symbol: str = Field(..., description="Ticker symbol, e.g., 'btc'")
    amount: float = Field(..., ge=0, description="Units held")

class Portfolio(BaseModel):
    name: str = Field(..., description="Portfolio name")
    address: Optional[str] = Field(None, description="Optional public wallet address for display")
    holdings: List[Holding] = Field(default_factory=list, description="List of coin holdings")
