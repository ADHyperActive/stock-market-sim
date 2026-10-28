import anvil.google.auth, anvil.google.drive, anvil.google.mail
from anvil.google.drive import app_files
"""Integrity check + diagnostics + interrupted-order recovery.

Ported from the classroom Apps Script integrity checker
(analyzeIntegrityData_ semantics) and journal recovery
(recoverJournalRowLocked_ semantics): ledger replay, duplicate detection,
per-row commit-math verification, and PREPARED-orphan resolution. The check
and diagnostics callables never write anything — they only read and report.
recover_prepared() is the one writer here: it only ever flips a stale
PREPARED row to ACCEPTED or ABORTED and never rewrites account balances.
Every callable is teacher-gated via teachers._teacher_sim.

Conventions mirrored from the engine (orders.py):
- money tolerance 0.011 per step (classroom journalAmounts_);
- BUY avg cost is commission-inclusive weighted average, rounded each step;
- SELL keeps average cost; zero shares resets it to 0;
- stored student_data.total_value is NOT checked: nothing writes it, the
  portfolio and teacher panel compute totals on the fly.
"""

from datetime import datetime, timezone

import anvil.server
from anvil.tables import app_tables

from teachers import _teacher_sim
from orders import _money

MONEY_TOL = 0.011
CASH_REPLAY_TOL = 0.02
AVG_REPLAY_TOL = 0.02
VALID_STATUSES = ("PREPARED", "ACCEPTED", "REJECTED", "ABORTED")


def _now():
  return datetime.now(timezone.utc)


def _close_enough(left, right, tol):
  try:
    return abs(float(left) - float(right)) <= tol
  except (TypeError, ValueError):
    return False


def _student_key(student):
  try:
    return str(student["email"] or "").strip().lower()
  except (TypeError, KeyError, AttributeError):
    return ""


def _check_accepted_math(tx):
  """Verify one ACCEPTED row's commit math. Returns a list of issues."""
  issues = []
  order_id = str(tx["order_id"] or "")
  side = str(tx["side"] or "")
  shares = tx["shares"]
  price = _money(tx["execution_price"] or 0)
  gross = _money(tx["gross_amount"] or 0)
  commission = _money(tx["commission"] or 0)
  cash_before = _money(tx["cash_before"] or 0)
  cash_after = _money(tx["cash_after"] or 0)
  shares_before = tx["shares_before"]
  shares_after = tx["shares_after"]
  avg_before = _money(tx["avg_cost_before"] or 0)
  avg_after = _money(tx["avg_cost_after"] or 0)
  realized = _money(tx["gain_loss"] or 0)

  if not isinstance(shares, (int, float)) or int(shares) != shares or shares <= 0:
    issues.append("%s: shares is not a positive whole number." % order_id)
    return issues
  shares = int(shares)
  if price <= 0:
    issues.append("%s: execution price is not positive." % order_id)
    return issues
  if not _close_enough(gross, _money(price * shares), MONEY_TOL):
    issues.append("%s: gross %.2f != price x shares." % (order_id, gross))
  if side == "BUY":
    if not _close_enough(cash_after, _money(cash_before - gross - commission), MONEY_TOL):
      issues.append("%s: cash-after does not match cash-before minus cost." % order_id)
    if shares_after != shares_before + shares:
      issues.append("%s: shares-after does not match shares-before plus shares." % order_id)
    if shares_after > 0:
      expect_avg = _money(((shares_before * avg_before) + gross + commission) / shares_after)
      if not _close_enough(avg_after, expect_avg, MONEY_TOL):
        issues.append("%s: average-cost-after does not match the weighted average." % order_id)
  elif side == "SELL":
    if not _close_enough(cash_after, _money(cash_before + gross - commission), MONEY_TOL):
      issues.append("%s: cash-after does not match cash-before plus proceeds." % order_id)
    if shares_after != shares_before - shares:
      issues.append("%s: shares-after does not match shares-before minus shares." % order_id)
    expect_gain = _money((price - avg_before) * shares - commission)
    if not _close_enough(realized, expect_gain, MONEY_TOL):
      issues.append("%s: realized gain does not match price minus average cost." % order_id)
    if shares_after == 0 and avg_after != 0:
      issues.append("%s: average cost should reset to 0 at zero shares." % order_id)
  else:
    issues.append("%s: side is neither BUY nor SELL." % order_id)
  submitted = tx["submitted_at"]
  executed = tx["executed_at"]
  if executed is None:
    issues.append("%s: ACCEPTED row has no execution timestamp." % order_id)
  elif submitted is not None and executed < submitted:
    issues.append("%s: execution timestamp predates submission." % order_id)
  return issues


@anvil.server.callable
def integrity_check(simcode):
  """Full ledger integrity sweep for one simulation. Read-only."""
  sim, _teacher = _teacher_sim(simcode)
  code = sim["simcode"]
  issues = []
  notes = []

  students = list(app_tables.student_data.search(simcode=code))
  holdings = list(app_tables.holdings.search(simulation=sim))
  ledger = list(app_tables.transactions.search(simulation=sim))

  known = {_student_key(s): s for s in students if _student_key(s)}

  seen_order_ids = set()
  seen_request_ids = set()
  for tx in ledger:
    order_id = str(tx["order_id"] or "")
    if not order_id:
      issues.append("A ledger row is missing its order ID.")
    elif order_id in seen_order_ids:
      issues.append("Duplicate order ID: %s." % order_id)
    else:
      seen_order_ids.add(order_id)
    rid = str(tx["request_id"] or "")
    if rid and not rid.startswith("INVALID:"):
      if rid in seen_request_ids:
        issues.append("Duplicate request ID: %s (replay risk)." % rid)
      else:
        seen_request_ids.add(rid)
    status = str(tx["status"] or "")
    if status not in VALID_STATUSES:
      issues.append("%s: unknown status %s." % (order_id or "A row", status))
    student = tx["student"]
    if student is None:
      if status != "REJECTED":
        issues.append("%s: row references no student account." % (order_id or "A row"))
    elif _student_key(student) not in known:
      issues.append("%s: row references an unknown student account." % order_id)
    if status == "PREPARED":
      prepared = tx["prepared_at"]
      age = ""
      if prepared is not None:
        try:
          age = " (%d seconds old)" % max(0, int((_now() - prepared).total_seconds()))
        except (TypeError, ValueError):
          age = ""
      issues.append("%s: PREPARED row never finalized — run recovery%s."
                    % (order_id, age))
    if status == "ACCEPTED":
      issues.extend(_check_accepted_math(tx))
    if status in ("REJECTED", "ABORTED") and tx["executed_at"] is not None:
      issues.append("%s: %s row carries an execution timestamp." % (order_id, status))

  accepted = sorted(
    [t for t in ledger if str(t["status"] or "") == "ACCEPTED"],
    key=lambda r: r["submitted_at"] or _now(),
  )
  cash_delta = {}
  position_shares = {}
  position_avg = {}
  for tx in accepted:
    key = _student_key(tx["student"])
    if not key:
      continue
    side = str(tx["side"] or "")
    shares = int(tx["shares"] or 0)
    gross = _money(tx["gross_amount"] or 0)
    commission = _money(tx["commission"] or 0)
    cash_delta[key] = _money(cash_delta.get(key, 0)
                             + (gross - commission if side == "SELL" else -(gross + commission)))
    pos = (key, str(tx["ticker"] or ""))
    owned, avg = position_shares.get(pos, 0), position_avg.get(pos, 0.0)
    if side == "BUY" and shares > 0:
      position_avg[pos] = _money(((owned * avg) + gross + commission) / (owned + shares))
      position_shares[pos] = owned + shares
    elif side == "SELL":
      position_shares[pos] = owned - shares
      if owned - shares <= 0:
        position_shares[pos] = 0
        position_avg[pos] = 0.0

  for student in students:
    key = _student_key(student)
    if not key:
      issues.append("A roster row has a blank email address.")
      continue
    start = _money(student["start_cash"] or 0)
    cash = _money(student["cash_balance"] or 0)
    if cash < -0.005:
      issues.append("%s: negative cash balance %.2f." % (key, cash))
    expect_cash = _money(start + cash_delta.get(key, 0))
    if not _close_enough(cash, expect_cash, CASH_REPLAY_TOL):
      issues.append("%s: cash %.2f does not replay to %.2f from the ledger."
                    % (key, cash, expect_cash))

  holding_keys = set()
  for h in holdings:
    key = _student_key(h["student"])
    ticker = str(h["ticker"] or "")
    pos = (key, ticker)
    if pos in holding_keys:
      issues.append("%s %s: duplicate holding row." % (key, ticker))
    holding_keys.add(pos)
    if key not in known:
      issues.append("%s %s: holding references an unknown student." % (key, ticker))
      continue
    shares = h["shares"]
    if not isinstance(shares, (int, float)) or int(shares) != shares or shares < 0:
      issues.append("%s %s: holding shares are not a whole number >= 0." % (key, ticker))
      continue
    replayed = position_shares.get(pos, 0)
    if int(shares) != replayed:
      issues.append("%s %s: holding shows %d share(s) but the ledger replays to %d."
                    % (key, ticker, int(shares), replayed))
    elif int(shares) > 0:
      avg = _money(h["avg_cost"] or 0)
      if not _close_enough(avg, position_avg.get(pos, 0.0), AVG_REPLAY_TOL):
        issues.append("%s %s: average cost %.2f does not replay to %.2f."
                      % (key, ticker, avg, position_avg.get(pos, 0.0)))
    else:
      notes.append("%s %s: zero-share holding row kept for history." % (key, ticker))
  for pos, replayed in sorted(position_shares.items()):
    if replayed > 0 and pos not in holding_keys:
      issues.append("%s %s: ledger shows %d share(s) with no holding row."
                    % (pos[0], pos[1], replayed))

  status = "PASS" if not issues else "FAIL"
  return {
    "ok": True,
    "status": status,
    "summary": ("%d student(s), %d holding(s), %d ledger row(s): %s."
                % (len(students), len(holdings), len(ledger), status)),
    "issues": issues,
    "notes": notes,
    "counts": {
      "students": len(students),
      "holdings": len(holdings),
      "transactions": len(ledger),
      "accepted": len(accepted),
    },
  }


@anvil.server.callable
def diagnostics(simcode):
  """System health for one simulation. Read-only; secrets never returned."""
  sim, _teacher = _teacher_sim(simcode)
  checks = []

  def record(name, passed, details):
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "details": details})

  try:
    students = len(app_tables.student_data.search(simcode=sim["simcode"]))
    holdings = len(app_tables.holdings.search(simulation=sim))
    ledger = len(app_tables.transactions.search(simulation=sim))
    try:
      prices = len(app_tables.prices.search())
      record("Tables readable", True,
             "%d students, %d holdings, %d ledger rows, %d cached prices."
             % (students, holdings, ledger, prices))
    except AttributeError:
      record("Tables readable", False,
             "Prices table missing in the running app — pull the latest schema. "
             "(%d students, %d holdings, %d ledger rows.)" % (students, holdings, ledger))
  except Exception as error:
    record("Tables readable", False, str(error)[:200])

  config = dict(sim["config"] or {})
  try:
    commission = float(config.get("commission", 0))
    record("Commission sane", commission >= 0, "commission=%s" % commission)
  except (TypeError, ValueError):
    record("Commission sane", False, "commission is not a number.")
  record("Simulation active", bool(sim["active"]), "open=%s" % bool(sim["open"]))

  try:
    import anvil.secrets
    has_key = bool(anvil.secrets.get_secret("alpaca_api"))
    has_secret = bool(anvil.secrets.get_secret("alpaca_secret"))
    record("Alpaca secrets present", has_key and has_secret,
           "key %s, secret %s (values never displayed)."
           % ("set" if has_key else "MISSING", "set" if has_secret else "MISSING"))
  except Exception as error:
    record("Alpaca secrets present", False, str(error)[:200])

  try:
    import market
    status = market.market_status()
    record("Market data reachable", True, str(status)[:200])
  except Exception as error:
    record("Market data reachable", False, str(error)[:200])

  try:
    rows = app_tables.prices.search()
    latest = None
    for row in rows:
      retrieved = row["retrieved_at"]
      if retrieved is not None and (latest is None or retrieved > latest):
        latest = retrieved
    if latest is None:
      record("Price cache fresh", True, "Cache empty — no refresh has run yet.")
    else:
      age = max(0, int((_now() - latest).total_seconds()))
      record("Price cache fresh", True, "Newest cache entry is %d seconds old." % age)
  except AttributeError:
    record("Price cache fresh", False, "Prices table missing in the running app.")
  except Exception as error:
    record("Price cache fresh", False, str(error)[:200])

  failed = [c["name"] for c in checks if c["status"] != "PASS"]
  status = "READY" if not failed else "NOT READY"
  return {
    "ok": True,
    "status": status,
    "summary": ("%s. Failing: %s." % (status, ", ".join(failed) if failed else "none")),
    "checks": checks,
  }


RECOVERY_MIN_AGE_SECONDS = 300


@anvil.server.callable
def recover_prepared(simcode, min_age_seconds=RECOVERY_MIN_AGE_SECONDS,
                     order_id="", dry_run=False):
  """Resolve stale PREPARED rows left by interrupted commits.

  Mirrors the classroom recoverJournalRowLocked_ decision rule using the
  account state alone (no price re-check — the row already carries its
  execution price):
  - account matches the row's post-state (cash_after / shares_after)
    -> the writes landed; finalize ACCEPTED.
  - account matches the row's pre-state (cash_before / shares_before)
    -> no write landed; ABORTED, no trade occurred.
  - anything else -> CONFLICT: left untouched for manual review.

  Only rows older than min_age_seconds are eligible, so a live commit in
  flight is never touched. dry_run=True reports the decisions without
  writing. Only the row's status/message/executed_at/completed_at are ever
  written — balances and holdings are only read.
  """
  sim, _teacher = _teacher_sim(str(simcode or "").strip().upper())
  try:
    min_age = max(60, int(min_age_seconds))
  except (TypeError, ValueError):
    min_age = RECOVERY_MIN_AGE_SECONDS
  only = str(order_id or "").strip()
  cutoff = _now().timestamp() - min_age
  actions = []
  counts = {"accepted": 0, "aborted": 0, "conflict": 0, "skipped": 0}
  for row in app_tables.transactions.search(simulation=sim, status="PREPARED"):
    row_id = str(row["order_id"] or "")
    if only and row_id != only:
      continue
    prepared = row["prepared_at"]
    try:
      age_ok = prepared is None or prepared.timestamp() <= cutoff
    except (AttributeError, TypeError, ValueError):
      age_ok = True
    if not age_ok:
      actions.append({"orderId": row_id, "action": "SKIPPED",
                      "detail": "Prepared too recently; left for the live commit."})
      counts["skipped"] += 1
      continue
    student = row["student"]
    if student is None:
      actions.append({"orderId": row_id, "action": "CONFLICT",
                      "detail": "Student link missing; manual review required."})
      counts["conflict"] += 1
      continue
    ticker = str(row["ticker"] or "")
    holdings = app_tables.holdings.search(simulation=sim, student=student,
                                          ticker=ticker)
    holding = holdings[0] if len(holdings) else None
    try:
      cash_now = float(student["cash_balance"] or 0)
    except (TypeError, ValueError):
      cash_now = 0.0
    shares_now = holding["shares"] if holding is not None else 0
    try:
      shares_now = int(shares_now)
    except (TypeError, ValueError):
      shares_now = 0
    try:
      shares_before = int(row["shares_before"] or 0)
      shares_after = int(row["shares_after"] or 0)
    except (TypeError, ValueError):
      shares_before = shares_after = -1
    post_match = (_close_enough(cash_now, row["cash_after"], MONEY_TOL)
                  and shares_now == shares_after)
    pre_match = (_close_enough(cash_now, row["cash_before"], MONEY_TOL)
                 and shares_now == shares_before)
    if post_match and not pre_match:
      action, detail = "ACCEPTED", "Account already reflects the trade; finalized."
    elif pre_match and not post_match:
      action, detail = "ABORTED", "No account write landed; no trade occurred."
    else:
      actions.append({"orderId": row_id, "action": "CONFLICT",
                      "detail": "Account matches neither pre- nor post-state; "
                                "manual review required."})
      counts["conflict"] += 1
      continue
    if not dry_run:
      stamp = _now()
      row["status"] = action
      row["message"] = "Recovered: " + detail
      if action == "ACCEPTED" and row["executed_at"] is None:
        row["executed_at"] = stamp
      row["completed_at"] = stamp
    actions.append({"orderId": row_id,
                    "action": action + (" (dry run)" if dry_run else ""),
                    "detail": detail})
    counts["accepted" if action == "ACCEPTED" else "aborted"] += 1
  summary = ("%d finalized, %d aborted, %d conflict(s), %d skipped%s." % (
    counts["accepted"], counts["aborted"], counts["conflict"],
    counts["skipped"], " (dry run)" if dry_run else ""))
  return {"ok": True, "simcode": sim["simcode"], "summary": summary,
          "actions": actions, "counts": counts}
