"""Order engine: journal-first simulated trading.

Ported from the classroom Apps Script Engine (commitOrderLocked_ semantics):
validate -> idempotency check -> account/trading gates -> quote ->
cash/share math -> PREPARED journal row -> apply state -> finalize
ACCEPTED. Rejections are journaled as REJECTED and change nothing.

Concurrency note: Anvil has no LockService. Same-account races are narrowed
by the two-phase journal (PREPARED row first, request_id re-checked at
finalize, losers marked ABORTED). This is check-then-write, not serializable
isolation — acceptable at classroom scale.

Quote provider below is a STUB (fixed test prices, classification TEST).
The alpaca-py milestone replaces get_execution_quote() only; commit math,
journal shape, and result shape stay unchanged.
"""

import re
import uuid
from datetime import datetime, timezone

import anvil.server
import anvil.users
from anvil.tables import app_tables

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
MAX_REQUEST_ID = 120
MAX_NOTE = 500
DEFAULT_COMMISSION = 0.0

# STUB prices. Every quote from this provider is marked classification TEST
# and must never be mistaken for market data.
TEST_PRICES = {
  "AAPL": 230.00,
  "MSFT": 420.00,
  "SPY": 500.00,
  "TSLA": 250.00,
  "NVDA": 130.00,
}
TEST_DEFAULT_PRICE = 100.00


def _now():
  return datetime.now(timezone.utc)


def _money(value):
  return round(float(value) + 1e-9, 2)


def _caller_email():
  user = anvil.users.get_user()
  if user is None:
    raise anvil.server.PermissionDenied("Log in first.")
  email = str(user["email"] or "").strip().lower()
  if not email:
    raise anvil.server.PermissionDenied("Your login has no email address.")
  return email


def _new_order_id():
  return "ORD-" + uuid.uuid4().hex[:12].upper()


def _validate_order(side, ticker, shares, note, request_id):
  """Returns (clean_dict, error_message). First failure wins, as in buildOrderContext_."""
  clean_side = str(side or "").strip().upper()
  if clean_side not in ("BUY", "SELL"):
    return None, "Order side must be BUY or SELL."
  clean_ticker = str(ticker or "").strip().upper()
  if not TICKER_RE.match(clean_ticker):
    return None, "Ticker may contain only letters, numbers, periods, and hyphens."
  if isinstance(shares, bool):
    return None, "Shares must be a whole number greater than zero."
  try:
    clean_shares = int(shares)
  except (TypeError, ValueError):
    return None, "Shares must be a whole number greater than zero."
  if float(shares) != clean_shares or clean_shares <= 0:
    if not isinstance(shares, int) and float(shares) != clean_shares:
      return None, "Fractional shares are disabled. Enter a whole number of shares."
    return None, "Shares must be a whole number greater than zero."
  clean_note = str(note or "").strip()[:MAX_NOTE]
  rid = str(request_id or "").strip()[:MAX_REQUEST_ID]
  if not rid:
    rid = "INVALID:" + uuid.uuid4().hex[:12].upper()
  elif not REQUEST_ID_RE.match(rid):
    return None, "The order request identifier is malformed."
  return {
    "side": clean_side,
    "ticker": clean_ticker,
    "shares": clean_shares,
    "note": clean_note,
    "request_id": rid,
  }, ""


def get_execution_quote(ticker, side):
  """STUB — replaced by the alpaca-py milestone. Fixed test prices."""
  price = _money(TEST_PRICES.get(ticker, TEST_DEFAULT_PRICE))
  now = _now()
  spread = _money(max(0.01, price * 0.001))
  return {
    "ok": True,
    "price": _money(price + spread) if side == "BUY" else _money(price - spread),
    "bid": _money(price - spread),
    "ask": _money(price + spread),
    "quote_time": now,
    "age_seconds": 0,
    "source": "TEST_STUB",
    "classification": "TEST",
    "market_state": "TEST_OPEN",
  }


def _journal_reject(sim, student, clean, message, quote):
  submitted = _now()
  app_tables.transactions.add_row(
    order_id=_new_order_id(),
    simulation=sim,
    student=student,
    submitted_at=submitted,
    executed_at=None,
    side=clean["side"] if clean else "",
    ticker=clean["ticker"] if clean else "",
    shares=clean["shares"] if clean else 0,
    execution_price=(quote or {}).get("price", 0) or 0,
    gross_amount=0,
    commission=0,
    cash_after=student["cash_balance"] if student else 0,
    shares_after=0,
    gain_loss=0,
    status="REJECTED",
    message=str(message)[:500],
    channel="ANVIL",
    quote_time=(quote or {}).get("quote_time"),
    quote_age_sec=(quote or {}).get("age_seconds", 0),
    order_note=clean["note"] if clean else "",
    request_id=clean["request_id"] if clean else "",
    market_state=(quote or {}).get("market_state", "NOT CHECKED"),
    data_source=(quote or {}).get("source", ""),
    price_retrieved=0,
    price_classification=(quote or {}).get("classification", "UNUSABLE"),
    prior_close="",
    cash_before=student["cash_balance"] if student else 0,
    shares_before=0,
    avg_cost_before=0,
    avg_cost_after=0,
    existed_before=False,
    prepared_at=submitted,
    completed_at=submitted,
  )
  return {"ok": False, "status": "REJECTED", "message": str(message)[:500]}


def _duplicate_result(prior):
  return {
    "ok": False,
    "status": "DUPLICATE",
    "duplicate": True,
    "orderId": prior["order_id"],
    "message": (
      "Duplicate submission ignored. Original order " + str(prior["order_id"])
      + " already has status " + str(prior["status"]) + "."
    ),
  }


def _holding_for(sim, student, ticker):
  return app_tables.holdings.get(simulation=sim, student=student, ticker=ticker)


@anvil.server.callable
def place_order(simcode, side, ticker, shares, note="", request_id=""):
  """Commit one BUY/SELL order for the caller in one simulation."""
  email = _caller_email()
  code = str(simcode or "").strip().upper()
  sim = app_tables.simulations.get(simcode=code, active=True)
  if sim is None:
    return {"ok": False, "status": "REJECTED", "message": "No active simulation uses that code."}

  clean, error = _validate_order(side, ticker, shares, note, request_id)
  if error:
    return _journal_reject(sim, None, clean, error, None)

  prior = app_tables.transactions.get(simulation=sim, request_id=clean["request_id"])
  if prior is not None:
    return _duplicate_result(prior)

  student = app_tables.student_data.get(simcode=code, email=email)
  if student is None:
    return _journal_reject(sim, None, clean, "No account for " + email + " in this simulation.", None)
  if not student["active"]:
    return _journal_reject(sim, student, clean, "This account is inactive.", None)

  config = dict(sim["config"] or {})
  if not sim["open"]:
    return _journal_reject(sim, student, clean, "Trading is currently closed by the teacher.", None)

  commission = _money(config.get("commission", DEFAULT_COMMISSION))
  quote = get_execution_quote(clean["ticker"], clean["side"])
  if not quote.get("ok"):
    return _journal_reject(sim, student, clean, "No trustworthy quote is available.", quote)

  price = _money(quote["price"])
  gross = _money(price * clean["shares"])
  cash_before = _money(student["cash_balance"])
  holding = _holding_for(sim, student, clean["ticker"])
  shares_before = int(holding["shares"]) if holding else 0
  avg_before = _money(holding["avg_cost"]) if holding else 0.0

  if clean["side"] == "BUY":
    total_cost = _money(gross + commission)
    if total_cost > cash_before + 0.005:
      return _journal_reject(
        sim, student, clean,
        "Insufficient cash. This order requires $%.2f, but the account has $%.2f."
        % (total_cost, cash_before), quote)
    shares_after = shares_before + clean["shares"]
    avg_after = _money(((shares_before * avg_before) + gross + commission) / shares_after)
    cash_after = _money(cash_before - total_cost)
    realized = 0.0
  else:
    if clean["shares"] > shares_before:
      return _journal_reject(
        sim, student, clean,
        "Insufficient shares. The account owns %d share(s) of %s."
        % (shares_before, clean["ticker"]), quote)
    net = _money(gross - commission)
    if net < 0:
      return _journal_reject(sim, student, clean,
        "The commission is greater than the sale proceeds.", quote)
    shares_after = shares_before - clean["shares"]
    avg_after = avg_before if shares_after > 0 else 0.0
    cash_after = _money(cash_before + net)
    realized = _money((price - avg_before) * clean["shares"] - commission)

  if cash_after < -0.005 or shares_after < 0:
    raise RuntimeError("Transaction invariant failed before commit.")

  submitted = _now()
  order_id = _new_order_id()
  # Write-ahead barrier: recoverable PREPARED row before any account write.
  row = app_tables.transactions.add_row(
    order_id=order_id,
    simulation=sim,
    student=student,
    submitted_at=submitted,
    executed_at=None,
    side=clean["side"],
    ticker=clean["ticker"],
    shares=clean["shares"],
    execution_price=price,
    gross_amount=gross,
    commission=commission,
    cash_after=cash_after,
    shares_after=shares_after,
    gain_loss=realized,
    status="PREPARED",
    message=clean["side"] + " order prepared, awaiting account commit.",
    channel="ANVIL",
    quote_time=quote["quote_time"],
    quote_age_sec=quote["age_seconds"],
    order_note=clean["note"],
    request_id=clean["request_id"],
    market_state=quote["market_state"],
    data_source=quote["source"],
    price_retrieved=0,
    price_classification=quote["classification"],
    prior_close="",
    cash_before=cash_before,
    shares_before=shares_before,
    avg_cost_before=avg_before,
    avg_cost_after=avg_after,
    existed_before=holding is not None,
    prepared_at=submitted,
    completed_at=None,
  )
  # Idempotency re-check inside the commit window: a concurrent duplicate
  # that slipped past the first check loses here and is marked ABORTED.
  rivals = app_tables.transactions.search(simulation=sim, request_id=clean["request_id"])
  if len(rivals) > 1:
    row["status"] = "ABORTED"
    row["message"] = "Lost commit race to an identical request; no trade occurred."
    row["completed_at"] = _now()
    first = min(rivals, key=lambda r: r["submitted_at"] or _now())
    return _duplicate_result(first)

  try:
    student["cash_balance"] = cash_after
    student["last_trade"] = submitted.isoformat()
    if holding is None:
      app_tables.holdings.add_row(
        simulation=sim, student=student, ticker=clean["ticker"],
        shares=shares_after, avg_cost=avg_after, last_updated=submitted)
    else:
      holding["shares"] = shares_after
      holding["avg_cost"] = avg_after
      holding["last_updated"] = submitted
  except Exception as error:
    row["status"] = "ABORTED"
    row["message"] = "Account write failed; no trade occurred: " + str(error)[:200]
    row["completed_at"] = _now()
    raise

  executed = _now()
  row["status"] = "ACCEPTED"
  row["message"] = (
    "%s %d share(s) of %s at $%.2f. Price source: %s (TEST DATA)."
    % (clean["side"], clean["shares"], clean["ticker"], price, quote["source"]))
  row["executed_at"] = executed
  row["completed_at"] = executed
  return {
    "ok": True,
    "orderId": order_id,
    "status": "ACCEPTED",
    "message": row["message"],
    "ticker": clean["ticker"],
    "side": clean["side"],
    "shares": clean["shares"],
    "price": price,
    "cashAfter": cash_after,
    "sharesAfter": shares_after,
    "marketDataSource": quote["source"],
    "priceClassification": quote["classification"],
  }


@anvil.server.callable
def recent_orders(simcode, limit=10):
  """Caller's recent orders in one simulation, newest first."""
  email = _caller_email()
  code = str(simcode or "").strip().upper()
  sim = app_tables.simulations.get(simcode=code)
  if sim is None:
    return {"ok": False, "message": "Unknown simulation code."}
  student = app_tables.student_data.get(simcode=code, email=email)
  if student is None:
    return {"ok": False, "message": "No account in this simulation."}
  rows = app_tables.transactions.search(simulation=sim, student=student)
  ordered = sorted(rows, key=lambda r: r["submitted_at"] or _now(), reverse=True)
  try:
    count = max(1, min(50, int(limit)))
  except (TypeError, ValueError):
    count = 10
  return {"ok": True, "orders": [
    {
      "orderId": r["order_id"],
      "submittedAt": (r["submitted_at"] or "").isoformat() if r["submitted_at"] else "",
      "status": r["status"],
      "side": r["side"],
      "shares": r["shares"],
      "ticker": r["ticker"],
      "price": r["execution_price"],
      "message": r["message"],
    }
    for r in ordered[:count]
  ]}


def _position_totals(sim, student):
  """Holdings valued at average cost. TEST-phase valuation: with stub
  quotes there is no market price yet, so unrealized gain is 0 by
  construction. The market-data milestone replaces the price source."""
  positions = []
  holdings_value = 0.0
  for h in app_tables.holdings.search(simulation=sim, student=student):
    shares = int(h["shares"] or 0)
    if shares <= 0:
      continue
    avg = _money(h["avg_cost"] or 0)
    value = _money(shares * avg)
    holdings_value = _money(holdings_value + value)
    positions.append({
      "ticker": h["ticker"],
      "shares": shares,
      "avgCost": avg,
      "price": avg,
      "marketValue": value,
      "unrealizedGain": 0.0,
      "returnRate": 0.0,
    })
  positions.sort(key=lambda p: p["marketValue"], reverse=True)
  return positions, holdings_value


@anvil.server.callable
def get_portfolio(simcode):
  """Caller's portfolio in one simulation: cash, positions, totals, rank."""
  email = _caller_email()
  code = str(simcode or "").strip().upper()
  sim = app_tables.simulations.get(simcode=code)
  if sim is None:
    return {"ok": False, "message": "Unknown simulation code."}
  student = app_tables.student_data.get(simcode=code, email=email)
  if student is None:
    return {"ok": False, "message": "No account for " + email + " in this simulation."}
  if not student["active"]:
    return {"ok": False, "message": "This account is inactive."}

  positions, holdings_value = _position_totals(sim, student)
  cash = _money(student["cash_balance"] or 0)
  start = _money(student["start_cash"] or 0)
  total = _money(cash + holdings_value)
  gain = _money(total - start)
  rate = (gain / start) if start else 0.0

  totals = []
  for other in app_tables.student_data.search(simcode=code, active=True):
    opos, oval = _position_totals(sim, other)
    totals.append((other["email"], _money(_money(other["cash_balance"] or 0) + oval)))
  totals.sort(key=lambda t: t[1], reverse=True)
  rank = next((i + 1 for i, t in enumerate(totals) if t[0] == email), None)

  return {
    "ok": True,
    "simcode": code,
    "tradingOpen": bool(sim["open"]),
    "testData": True,
    "student": {
      "name": str(student["first_name"] or "") + " " + str(student["last_name"] or ""),
      "email": email,
      "classPeriod": str(student["class_period"] or ""),
      "cashBalance": cash,
      "holdingsValue": holdings_value,
      "totalValue": total,
      "dollarGainLoss": gain,
      "returnRate": rate,
      "rank": rank,
      "rankOf": len(totals),
    },
    "holdings": positions,
  }


@anvil.server.callable
def preview_quote(simcode, ticker):
  """Live price preview for the ticker pill. Read-only; never places orders.
  Caller must be the teacher of the sim or an active student in it."""
  email = _caller_email()
  code = str(simcode or "").strip().upper()
  sim = app_tables.simulations.get(simcode=code)
  if sim is None:
    return {"ok": False, "message": "Unknown simulation code."}
  is_teacher = str(sim["teacher_email"] or "").strip().lower() == email
  if not is_teacher:
    student = app_tables.student_data.get(simcode=code, email=email)
    if student is None or not student["active"]:
      return {"ok": False, "message": "No active account in this simulation."}
  clean = str(ticker or "").strip().upper()
  if not TICKER_RE.match(clean):
    return {"ok": False, "message": "Enter a valid ticker symbol."}
  quote = get_execution_quote(clean, "BUY")
  if not quote.get("ok"):
    return {"ok": False, "message": "No price available for " + clean + "."}
  return {
    "ok": True,
    "ticker": clean,
    "price": quote["price"],
    "bid": quote.get("bid", ""),
    "ask": quote.get("ask", ""),
    "source": quote.get("source", ""),
    "classification": quote.get("classification", ""),
  }
