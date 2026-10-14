"""Live market data via alpaca-py: execution quotes + valuation prices.

This module replaces the TEST stub that used to live in
orders.get_execution_quote. Nothing else in the engine changes: the commit
math, journal shape, and result shape are untouched.

Credentials: the Alpaca key ID/secret are read from Anvil secrets
("alpaca_api" / "alpaca_secret", the sandbox owner's own paper keys) at call
time. They are never logged, never stored in tables, and never sent to the
client.

Pricing rules (ported from the classroom engine):
- Regular session: fresh IEX quote only. BUY executes at the ask, SELL at the
  bid. Free-tier keys have no consolidated SIP feed, so there is no delayed
  fallback: a stale quote is a safe reject, not an execution.
- Pre-market: weekdays from 08:00 ET until the regular open. Same
  side-specific rule (BUY=ask, SELL=bid), quote age capped at 90 seconds,
  two-sided non-crossed quote with positive sizes required.
- Valuation (portfolio display) is tolerant: quote midpoint, else latest
  trade, else previous close marked stale. Display never blocks on data.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import anvil.secrets
import anvil.server
from anvil.tables import app_tables

try:
  from alpaca.trading.client import TradingClient
  from alpaca.data.historical import StockHistoricalDataClient
  from alpaca.data.requests import StockLatestQuoteRequest, StockSnapshotRequest
  _ALPACA_IMPORT_ERROR = ""
except Exception as error:  # alpaca-py missing from server requirements
  TradingClient = None
  StockHistoricalDataClient = None
  StockLatestQuoteRequest = None
  StockSnapshotRequest = None
  _ALPACA_IMPORT_ERROR = str(error)

ET = ZoneInfo("America/New_York")
PRE_MARKET_START = "08:00"
DEFAULT_FRESHNESS_SECONDS = 90
DEFAULT_VALUATION_TTL_SECONDS = 120
CLOCK_TTL_SECONDS = 60

_clock_cache = {"at": None, "state": None}


def _now():
  return datetime.now(timezone.utc)


def _money(value):
  return round(float(value) + 1e-9, 2)


def _freshness_seconds(config):
  try:
    return max(15, int((config or {}).get("quoteFreshnessSeconds", DEFAULT_FRESHNESS_SECONDS)))
  except (TypeError, ValueError):
    return DEFAULT_FRESHNESS_SECONDS


def _clients():
  """Lazily built Alpaca clients. Raises a user-safe message on any problem."""
  if _ALPACA_IMPORT_ERROR:
    raise RuntimeError("Market data is unavailable (alpaca-py is not installed).")
  try:
    key = anvil.secrets.get_secret("alpaca_api")
    secret = anvil.secrets.get_secret("alpaca_secret")
  except Exception:
    raise RuntimeError("Market-data credentials are not configured.")
  if not key or not secret:
    raise RuntimeError("Market-data credentials are not configured.")
  return (
    TradingClient(key, secret, paper=True),
    StockHistoricalDataClient(key, secret),
  )


def _clock_state():
  """Cached Alpaca market clock: {is_open, next_open, next_close, checked_at}."""
  now = _now()
  cached = _clock_cache["state"]
  if cached and _clock_cache["at"] and (now - _clock_cache["at"]).total_seconds() <= CLOCK_TTL_SECONDS:
    return cached
  trading, _data = _clients()
  try:
    clock = trading.get_clock()
  except Exception as error:
    raise RuntimeError(_provider_message(error))
  state = {
    "is_open": bool(clock.is_open),
    "next_open": clock.next_open,
    "next_close": clock.next_close,
    "checked_at": now,
  }
  _clock_cache["at"] = now
  _clock_cache["state"] = state
  return state


def _provider_message(error):
  """Map provider failures to safe messages (never leaks secrets)."""
  status = getattr(error, "status_code", None) or getattr(error, "status", None)
  try:
    status = int(status) if status is not None else None
  except (TypeError, ValueError):
    status = None
  text = str(error or "")[:200]
  if status == 401 or status == 403:
    return "Market-data credentials were rejected."
  if status == 404 or status == 422 or "not found" in text.lower():
    return "Unknown or unsupported ticker."
  if status == 429:
    return "Market-data rate limit reached. Try again shortly."
  return "Market data is temporarily unavailable."


def _quote_age(quote_time, now):
  if quote_time is None:
    return None
  stamp = quote_time
  if stamp.tzinfo is None:
    stamp = stamp.replace(tzinfo=timezone.utc)
  return (now - stamp).total_seconds()


def _premarket_session(clock, now):
  """True on a weekday from 08:00 ET until today's regular open."""
  et_now = now.astimezone(ET)
  if et_now.weekday() >= 5:
    return False
  if clock["is_open"]:
    return False
  if et_now.strftime("%H:%M") < PRE_MARKET_START:
    return False
  next_open = clock["next_open"]
  if next_open is None:
    return False
  if next_open.tzinfo is None:
    next_open = next_open.replace(tzinfo=timezone.utc)
  return next_open.astimezone(ET).date() == et_now.date()


def _two_sided(quote):
  try:
    bid = float(quote.bid_price)
    ask = float(quote.ask_price)
    bid_size = float(quote.bid_size or 0)
    ask_size = float(quote.ask_size or 0)
  except (TypeError, ValueError):
    return None
  if bid <= 0 or ask <= 0 or ask < bid or bid_size <= 0 or ask_size <= 0:
    return None
  return bid, ask


def get_live_quote(ticker, side, config=None):
  """Execution quote for one order. Classroom-shaped result dict.

  Returns ok True with price/bid/ask/quote_time/age_seconds/source/
  classification/market_state, or ok False with a safe message.
  """
  cfg = config or {}
  limit = _freshness_seconds(cfg)
  now = _now()
  try:
    clock = _clock_state()
  except RuntimeError as error:
    return {"ok": False, "message": str(error)}
  if clock["is_open"]:
    session = ("OPEN", "LIVE_REAL_TIME", "ALPACA_IEX")
  elif _premarket_session(clock, now):
    session = ("PRE_MARKET", "LIVE_PRE_MARKET", "ALPACA_IEX_PREMARKET")
    limit = min(limit, 90)
  else:
    return {"ok": False, "message": "The market is closed and morning trading is unavailable."}
  market_state, classification, source = session
  try:
    _trading, data = _clients()
    quotes = data.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=[ticker], feed="iex"))
  except Exception as error:
    return {"ok": False, "message": _provider_message(error)}
  quote = (quotes or {}).get(ticker)
  if quote is None:
    return {"ok": False, "message": "Unknown or unsupported ticker."}
  sides = _two_sided(quote)
  if sides is None:
    return {"ok": False, "message": "No trustworthy quote is available for " + ticker + "."}
  bid, ask = sides
  age = _quote_age(quote.timestamp, now)
  if age is None or age < 0 or age > limit:
    return {"ok": False, "message": "The quote for " + ticker + " is too old to trade on."}
  price = ask if side == "BUY" else bid
  return {
    "ok": True,
    "price": _money(price),
    "bid": _money(bid),
    "ask": _money(ask),
    "quote_time": quote.timestamp,
    "age_seconds": age,
    "source": source,
    "classification": classification,
    "market_state": market_state,
  }


def _valuation_ttl(config):
  try:
    minutes = int((config or {}).get("quoteRefreshMinutes", 1))
  except (TypeError, ValueError):
    minutes = 1
  return max(60, minutes * 60, DEFAULT_VALUATION_TTL_SECONDS)


def _read_cache(tickers, ttl):
  """Prices-table cache: {ticker: {price, quote_time, stale}} for fresh rows."""
  now = _now()
  found = {}
  try:
    rows = app_tables.prices.search()
  except Exception:
    return found
  for row in rows:
    ticker = row["ticker"]
    if ticker not in tickers or ticker in found:
      continue
    retrieved = row["retrieved_at"]
    if retrieved is not None and retrieved.tzinfo is None:
      retrieved = retrieved.replace(tzinfo=timezone.utc)
    if retrieved is not None and (now - retrieved).total_seconds() <= ttl:
      found[ticker] = {
        "price": _money(row["price"] or 0),
        "quote_time": row["quote_time"],
        "stale": False,
      }
  return found


def _snapshot_batch(tickers):
  """One batched Alpaca snapshot call for many tickers. Never raises."""
  try:
    _trading, data = _clients()
    return data.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=list(tickers), feed="iex")) or {}
  except Exception:
    return {}


def _display_price(snapshot):
  """Valuation-only price: quote midpoint, else latest trade, else prev close."""
  quote = getattr(snapshot, "latest_quote", None)
  if quote is not None:
    sides = _two_sided(quote)
    if sides is not None:
      return _money((sides[0] + sides[1]) / 2), quote.timestamp, False
  trade = getattr(snapshot, "latest_trade", None)
  if trade is not None:
    try:
      price = float(trade.price)
    except (TypeError, ValueError):
      price = 0
    if price > 0:
      return _money(price), trade.timestamp, True
  bar = getattr(snapshot, "previous_daily_bar", None)
  if bar is not None:
    try:
      price = float(bar.close)
    except (TypeError, ValueError):
      price = 0
    if price > 0:
      return _money(price), bar.timestamp, True
  return None, None, True


def get_valuation_prices(tickers, config=None):
  """Display prices for many tickers: {ticker: {price, quote_time, stale}}.

  Cache-first (prices table), one batched snapshot call for the rest.
  Missing tickers are simply absent. Never raises.
  """
  wanted = [t for t in dict.fromkeys(tickers or []) if t]
  if not wanted:
    return {}
  cfg = config or {}
  now = _now()
  prices = _read_cache(wanted, _valuation_ttl(cfg))
  missing = [t for t in wanted if t not in prices]
  if not missing:
    return prices
  snapshots = _snapshot_batch(missing)
  for ticker in missing:
    snapshot = snapshots.get(ticker)
    if snapshot is None:
      continue
    price, stamp, stale = _display_price(snapshot)
    if price is None:
      continue
    prices[ticker] = {"price": price, "quote_time": stamp, "stale": stale}
    try:
      existing = app_tables.prices.get(ticker=ticker)
      if existing:
        existing.update(
          price=price, quote_time=stamp, source="ALPACA_IEX_SNAPSHOT",
          classification="LIVE_DELAYED_OK" if stale else "LIVE_REAL_TIME",
          retrieved_at=now,
        )
      else:
        app_tables.prices.add_row(
          ticker=ticker, price=price, quote_time=stamp, source="ALPACA_IEX_SNAPSHOT",
          classification="LIVE_DELAYED_OK" if stale else "LIVE_REAL_TIME",
          retrieved_at=now,
        )
    except Exception:
      pass
  return prices


@anvil.server.callable
def market_status():
  """Market clock for display: open/pre-market/closed. Never raises."""
  try:
    clock = _clock_state()
  except RuntimeError:
    return {"ok": False, "mode": "UNKNOWN"}
  now = _now()
  if clock["is_open"]:
    mode = "OPEN"
  elif _premarket_session(clock, now):
    mode = "PRE_MARKET"
  else:
    mode = "CLOSED"
  return {"ok": True, "mode": mode}
