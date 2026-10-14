"""Teacher panel + debug portal. Every callable is teacher-gated: the caller
must own the simulation. This module never touches production systems; it
operates only on this app's own tables with fictional users.

Debug order placement mirrors orders.place_order's commit core with channel
"DEBUG". The quote source (orders.get_execution_quote) and all small helpers
are imported, never copied — only the straight-line commit math is mirrored,
and it is marked as such. If the engine changes, mirror it here.
"""

import re

import anvil.server
import anvil.users
from anvil.tables import app_tables
from orders import (
  _validate_order,
  _duplicate_result,
  _holding_for,
  _new_order_id,
  _money,
  _now,
  _position_totals,
  get_execution_quote,
  DEFAULT_COMMISSION,
)

import market

STARTING_CASH = 10000  # Must match auth.STARTING_CASH.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
MAX_NAME = 80
MAX_PERIOD = 20
MAX_EMAIL = 120


def _teacher_sim(simcode):
  """(sim, email) if the caller owns this simulation, else raise."""
  user = anvil.users.get_user()
  if user is None:
    raise anvil.server.PermissionDenied("Log in first.")
  email = str(user["email"] or "").strip().lower()
  code = str(simcode or "").strip().upper()
  sim = app_tables.simulations.get(simcode=code)
  if sim is None or str(sim["teacher_email"] or "").strip().lower() != email:
    raise anvil.server.PermissionDenied("Teacher only.")
  return sim, email


def _clean_email(value):
  email = str(value or "").strip().lower()[:MAX_EMAIL]
  if not EMAIL_RE.match(email):
    return None
  return email


@anvil.server.callable
def teacher_panel(simcode):
  """Settings, counts, and roster for the teacher panel."""
  sim, _teacher = _teacher_sim(simcode)
  config = dict(sim["config"] or {})
  roster = []
  for student in app_tables.student_data.search(simcode=sim["simcode"]):
    _positions, value = _position_totals(sim, student)
    cash = _money(student["cash_balance"] or 0)
    roster.append({
      "email": student["email"],
      "name": (str(student["first_name"] or "") + " "
               + str(student["last_name"] or "")).strip(),
      "classPeriod": str(student["class_period"] or ""),
      "active": bool(student["active"]),
      "cashBalance": cash,
      "totalValue": _money(cash + value),
    })
  roster.sort(key=lambda r: r["email"])
  code = sim["simcode"]
  return {
    "ok": True,
    "simcode": code,
    "open": bool(sim["open"]),
    "active": bool(sim["active"]),
    "config": config,
    "counts": {
      "students": len(roster),
      "holdings": len(app_tables.holdings.search(simulation=sim)),
      "transactions": len(app_tables.transactions.search(simulation=sim)),
    },
    "roster": roster,
  }


@anvil.server.callable
def teacher_set_settings(simcode, patch):
  """Validate and store teacher settings. The engine reads commission,
  quoteFreshnessSeconds, preMarketEnabled, allowStaleQuotes, and
  staleMaxAgeSeconds from the stored config; the rest are stored for display
  and future milestones."""
  sim, _teacher = _teacher_sim(simcode)
  patch = patch or {}
  try:
    commission = float(patch.get("commission", DEFAULT_COMMISSION))
  except (TypeError, ValueError):
    return {"ok": False, "message": "Commission must be a number."}
  if commission < 0:
    return {"ok": False, "message": "Commission cannot be negative."}
  try:
    refresh_min = int(patch.get("quoteRefreshMinutes", 1))
    fresh_sec = int(patch.get("quoteFreshnessSeconds", 90))
    delayed_max = int(patch.get("delayedSipMaxAgeSeconds", 1200))
  except (TypeError, ValueError):
    return {"ok": False, "message": "Refresh settings must be whole numbers."}
  if not 1 <= refresh_min <= 60:
    return {"ok": False, "message": "Quote refresh minutes must be 1-60."}
  if not 15 <= fresh_sec <= 600:
    return {"ok": False, "message": "Quote freshness seconds must be 15-600."}
  if not 60 <= delayed_max <= 7200:
    return {"ok": False, "message": "Delayed max age seconds must be 60-7200."}
  allow_stale = bool(patch.get("allowStaleQuotes", False))
  try:
    stale_max = int(patch.get("staleMaxAgeSeconds", 86400))
  except (TypeError, ValueError):
    return {"ok": False, "message": "Stale max age must be a whole number of seconds."}
  if not 300 <= stale_max <= 604800:
    return {"ok": False, "message": "Stale max age seconds must be 300-604800."}
  benchmark = str(patch.get("benchmarkTicker", "SPY") or "SPY").strip().upper()
  if not TICKER_RE.match(benchmark):
    return {"ok": False, "message": "Benchmark ticker is invalid."}
  config = dict(sim["config"] or {})
  config.update({
    "commission": _money(commission),
    "quoteRefreshMinutes": refresh_min,
    "quoteFreshnessSeconds": fresh_sec,
    "delayedSipMaxAgeSeconds": delayed_max,
    "benchmarkTicker": benchmark,
    "allowStaleQuotes": allow_stale,
    "staleMaxAgeSeconds": stale_max,
  })
  sim["open"] = bool(patch.get("open", sim["open"]))
  sim["config"] = config
  return {"ok": True, "open": bool(sim["open"]), "config": config}


def _add_student_row(sim, email, first, last, period):
  """Shared roster insert. Returns (row, error_message)."""
  if app_tables.student_data.get(simcode=sim["simcode"], email=email) is not None:
    return None, "An account already exists for " + email + "."
  row = app_tables.student_data.add_row(
    email=email,
    first_name=first,
    last_name=last,
    class_period=period,
    active=True,
    start_cash=STARTING_CASH,
    cash_balance=STARTING_CASH,
    holdings_value=0,
    total_value=STARTING_CASH,
    gain_loss=0,
    simcode=sim["simcode"],
    **{"return": 0},
  )
  return row, ""


@anvil.server.callable
def teacher_create_student(simcode, email, first_name, last_name, class_period):
  """Roster a real student account (teacher panel)."""
  sim, _teacher = _teacher_sim(simcode)
  clean_email = _clean_email(email)
  if clean_email is None:
    return {"ok": False, "message": "Enter a valid email address."}
  first = str(first_name or "").strip()[:MAX_NAME]
  last = str(last_name or "").strip()[:MAX_NAME]
  period = str(class_period or "").strip()[:MAX_PERIOD]
  if not first or not last or not period:
    return {"ok": False, "message": "Enter a first name, last name, and class period."}
  _row, error = _add_student_row(sim, clean_email, first, last, period)
  if error:
    return {"ok": False, "message": error}
  return {"ok": True, "email": clean_email}


@anvil.server.callable
def debug_create_student(simcode, email):
  """Create a fictional debug account. Name/period are derived placeholders."""
  sim, _teacher = _teacher_sim(simcode)
  clean_email = _clean_email(email)
  if clean_email is None:
    return {"ok": False, "message": "Enter a valid email address."}
  local = clean_email.split("@")[0].replace(".", " ").strip() or "Debug"
  _row, error = _add_student_row(sim, clean_email, "Debug", local[:MAX_NAME], "DEBUG")
  if error:
    return {"ok": False, "message": error}
  return {"ok": True, "email": clean_email}


@anvil.server.callable
def debug_portfolio_as(simcode, target_email):
  """get_portfolio for an explicit account. Teacher only."""
  sim, _teacher = _teacher_sim(simcode)
  email = str(target_email or "").strip().lower()
  student = app_tables.student_data.get(simcode=sim["simcode"], email=email)
  if student is None:
    return {"ok": False, "message": "No account for " + email + " in this simulation."}
  if not student["active"]:
    return {"ok": False, "message": "This account is inactive."}
  config = dict(sim["config"] or {})
  tickers = set()
  for h in app_tables.holdings.search(simulation=sim):
    if int(h["shares"] or 0) > 0 and h["ticker"]:
      tickers.add(h["ticker"])
  live = market.get_valuation_prices(sorted(tickers), config)
  positions, holdings_value = _position_totals(sim, student, live)
  cash = _money(student["cash_balance"] or 0)
  start = _money(student["start_cash"] or 0)
  total = _money(cash + holdings_value)
  gain = _money(total - start)
  rate = (gain / start) if start else 0.0
  totals = []
  for other in app_tables.student_data.search(simcode=sim["simcode"], active=True):
    _opos, oval = _position_totals(sim, other, live)
    totals.append((other["email"], _money(_money(other["cash_balance"] or 0) + oval)))
  totals.sort(key=lambda t: t[1], reverse=True)
  rank = next((i + 1 for i, t in enumerate(totals) if t[0] == email), None)
  return {
    "ok": True,
    "simcode": sim["simcode"],
    "tradingOpen": bool(sim["open"]),
    "testData": False,
    "marketStatus": market.market_status(),
    "student": {
      "name": (str(student["first_name"] or "") + " "
               + str(student["last_name"] or "")).strip(),
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
def debug_recent_as(simcode, target_email, limit=10):
  """recent_orders for an explicit account. Teacher only."""
  sim, _teacher = _teacher_sim(simcode)
  email = str(target_email or "").strip().lower()
  student = app_tables.student_data.get(simcode=sim["simcode"], email=email)
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
      "submittedAt": r["submitted_at"].isoformat() if r["submitted_at"] else "",
      "status": r["status"],
      "side": r["side"],
      "shares": r["shares"],
      "ticker": r["ticker"],
      "price": r["execution_price"],
      "message": r["message"],
    }
    for r in ordered[:count]
  ]}


def _debug_reject(sim, student, clean, message, quote):
  """_journal_reject with channel DEBUG. Mirrors orders._journal_reject."""
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
    channel="DEBUG",
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


@anvil.server.callable
def debug_place_order(simcode, target_email, side, ticker, shares,
                      note="", request_id=""):
  """Place an order as an explicit account. Teacher only.

  Commit core mirrors orders.place_order line-for-line except channel DEBUG.
  Quote comes from the shared orders.get_execution_quote.
  """
  sim, _teacher = _teacher_sim(simcode)
  code = sim["simcode"]
  email = str(target_email or "").strip().lower()

  clean, error = _validate_order(side, ticker, shares, note, request_id)
  if error:
    return _debug_reject(sim, None, clean, error, None)

  prior = app_tables.transactions.get(simulation=sim, request_id=clean["request_id"])
  if prior is not None:
    return _duplicate_result(prior)

  student = app_tables.student_data.get(simcode=code, email=email)
  if student is None:
    return _debug_reject(sim, None, clean, "No account for " + email + " in this simulation.", None)
  if not student["active"]:
    return _debug_reject(sim, student, clean, "This account is inactive.", None)

  config = dict(sim["config"] or {})
  if not sim["open"]:
    return _debug_reject(sim, student, clean, "Trading is currently closed by the teacher.", None)

  commission = _money(config.get("commission", DEFAULT_COMMISSION))
  quote = get_execution_quote(clean["ticker"], clean["side"], config)
  if not quote.get("ok"):
    return _debug_reject(sim, student, clean, "No trustworthy quote is available.", quote)

  price = _money(quote["price"])
  gross = _money(price * clean["shares"])
  cash_before = _money(student["cash_balance"])
  holding = _holding_for(sim, student, clean["ticker"])
  shares_before = int(holding["shares"]) if holding else 0
  avg_before = _money(holding["avg_cost"]) if holding else 0.0

  if clean["side"] == "BUY":
    total_cost = _money(gross + commission)
    if total_cost > cash_before + 0.005:
      return _debug_reject(
        sim, student, clean,
        "Insufficient cash. This order requires $%.2f, but the account has $%.2f."
        % (total_cost, cash_before), quote)
    shares_after = shares_before + clean["shares"]
    avg_after = _money(((shares_before * avg_before) + gross + commission) / shares_after)
    cash_after = _money(cash_before - total_cost)
    realized = 0.0
  else:
    if clean["shares"] > shares_before:
      return _debug_reject(
        sim, student, clean,
        "Insufficient shares. The account owns %d share(s) of %s."
        % (shares_before, clean["ticker"]), quote)
    net = _money(gross - commission)
    if net < 0:
      return _debug_reject(sim, student, clean,
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
    channel="DEBUG",
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
    "%s %d share(s) of %s at $%.2f. Price source: %s (%s)."
    % (clean["side"], clean["shares"], clean["ticker"], price, quote["source"],
       "STALE DATA — older cached price"
       if str(quote["classification"] or "").startswith("STALE") else "LIVE DATA"))
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
def my_simulations():
  """Simulations the caller can re-open: owned (teacher) + joined (student)."""
  user = anvil.users.get_user()
  if user is None:
    return {"logged_in": False, "owned": [], "joined": []}
  email = str(user["email"] or "").strip().lower()
  owned = [
    {"simcode": s["simcode"], "open": bool(s["open"]), "active": bool(s["active"])}
    for s in app_tables.simulations.search(teacher_email=email)
  ]
  joined = []
  for row in app_tables.student_data.search(email=email):
    sim = app_tables.simulations.get(simcode=row["simcode"])
    if sim is None:
      continue
    joined.append({
      "simcode": row["simcode"],
      "open": bool(sim["open"]),
      "active": bool(sim["active"]),
    })
  return {"logged_in": True, "owned": owned, "joined": joined}


@anvil.server.callable
def teacher_leaderboard(simcode):
  """Per-student totals for every active account, ranked best first.
  Teacher only. Same valuation convention as the student portal."""
  sim, _teacher = _teacher_sim(simcode)
  board = []
  for student in app_tables.student_data.search(simcode=sim["simcode"], active=True):
    _positions, value = _position_totals(sim, student)
    cash = _money(student["cash_balance"] or 0)
    start = _money(student["start_cash"] or 0)
    total = _money(cash + value)
    gain = _money(total - start)
    board.append({
      "email": student["email"],
      "name": (str(student["first_name"] or "") + " "
               + str(student["last_name"] or "")).strip(),
      "classPeriod": str(student["class_period"] or ""),
      "cash": cash,
      "holdings": _money(value),
      "total": total,
      "gain": gain,
      "return": (gain / start) if start else 0,
    })
  board.sort(key=lambda r: (-r["total"], r["name"]))
  for rank, row in enumerate(board, start=1):
    row["rank"] = rank
  return {"ok": True, "simcode": sim["simcode"], "board": board}


@anvil.server.callable
def teacher_all_transactions(simcode, limit=200):
  """Every journal row in this simulation, newest first. Teacher only."""
  sim, _teacher = _teacher_sim(simcode)
  try:
    count = max(1, min(500, int(limit)))
  except (TypeError, ValueError):
    count = 200
  rows = app_tables.transactions.search(simulation=sim)
  ordered = sorted(rows, key=lambda r: r["submitted_at"] or _now(), reverse=True)
  return {"ok": True, "simcode": sim["simcode"], "orders": [
    {
      "orderId": r["order_id"],
      "submittedAt": r["submitted_at"].isoformat() if r["submitted_at"] else "",
      "email": r["student"]["email"] if r["student"] else "",
      "name": ((str(r["student"]["first_name"] or "") + " "
                + str(r["student"]["last_name"] or "")).strip()
               if r["student"] else ""),
      "side": r["side"],
      "shares": r["shares"],
      "ticker": r["ticker"],
      "price": r["execution_price"],
      "status": r["status"],
      "message": r["message"],
    }
    for r in ordered[:count]
  ]}


@anvil.server.callable
def debug_raw_quote(simcode, ticker):
  """Raw IEX book dump for diagnosing trust rejections. Teacher only."""
  sim, _teacher = _teacher_sim(simcode)
  return market.raw_quote(ticker)
