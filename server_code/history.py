import anvil.google.auth, anvil.google.drive, anvil.google.mail
from anvil.google.drive import app_files
"""Snapshots + valuation refresh, runnable as Anvil background tasks.

What one run does (per simulation):
  1. Refresh the prices-table cache for every held ticker plus the
     benchmark ticker (one batched Alpaca snapshot call).
  2. Write one snapshot row per active student: cash, holdings at live
     prices, totals, rank, benchmark price/return. Same-day re-runs skip
     students already snapshotted (ET date).

Two launch paths share the same worker:
  - Teacher button: launch_refresh_and_snapshot() enforces the teacher
    gate, then fires background_refresh_and_snapshot() via
    anvil.server.launch_background_task and returns immediately.
  - Anvil Scheduled Tasks (owner attaches in the editor as a plain
    interval): scheduled_refresh_all() enforces the run window in code
    (weekdays, 09:00-18:00 ET) because the scheduler has no quiet hours,
    then launches one background task per sim.

Background workers run without a user session, so the worker trusts only
the simcode and re-validates the sim row; the teacher gate lives in the
launcher. Secrets stay server-side inside the market module. Workers never
raise to the client; failures are returned as result dicts (and logged).

recent_history() gives the student portal its return-over-time series:
the caller's own snapshots (date, returnRate, benchmarkReturn).
"""

from datetime import datetime, timezone

import anvil.server
import anvil.users
from anvil.tables import app_tables

import market
from orders import _money, _position_totals
from teachers import _teacher_sim


def _et_now():
  """Current time in the market timezone (market.ET is America/New_York)."""
  return datetime.now(timezone.utc).astimezone(market.ET)


def _in_run_window(now=None):
  """True on weekdays 09:00-18:00 ET. The interval scheduler cannot do
  quiet hours, so this gate keeps nights and weekends from launching
  pointless background tasks and Alpaca calls."""
  et = now or _et_now()
  return et.weekday() < 5 and 9 <= et.hour < 18


def _benchmark_ticker(config):
  ticker = str((config or {}).get("benchmarkTicker", "SPY") or "SPY").strip().upper()
  return ticker or "SPY"


def _benchmark_start(sim):
  """First recorded benchmark price for the sim (None until first snapshot)."""
  rows = app_tables.snapshots.search(simulation=sim)
  earliest = None
  for row in rows:
    stamp = row["snapshot_at"]
    if stamp is None:
      continue
    if earliest is None or stamp < earliest["snapshot_at"]:
      earliest = row
  if earliest is None:
    return None
  try:
    start = float(earliest["benchmark_price"] or 0)
  except (TypeError, ValueError):
    return None
  return start if start > 0 else None


def refresh_and_snapshot(simcode, force_snapshot=False):
  """One refresh + snapshot pass for a sim. Returns a result dict.

  Prices always refresh. Snapshot rows are end-of-day records, so they
  are written only after the close (ET hour >= 16) unless force_snapshot
  is true (teacher button).
  """
  code = str(simcode or "").strip().upper()
  sim = app_tables.simulations.get(simcode=code)
  if sim is None:
    return {"ok": False, "message": "Unknown simulation code."}
  config = dict(sim["config"] or {})
  now = _et_now()
  date_str = now.strftime("%Y-%m-%d")
  snapshot_due = bool(force_snapshot or now.hour >= 16)

  tickers = set()
  for h in app_tables.holdings.search(simulation=sim):
    if int(h["shares"] or 0) > 0 and h["ticker"]:
      tickers.add(h["ticker"])
  benchmark = _benchmark_ticker(config)
  tickers.add(benchmark)
  live = market.get_valuation_prices(sorted(tickers), config)

  already = set()
  for row in app_tables.snapshots.search(simulation=sim, date_str=date_str):
    student = row["student"]
    if student is not None:
      already.add(student["email"])

  students = [s for s in app_tables.student_data.search(simcode=code, active=True)]
  totals = []
  valued = []
  for student in students:
    positions, holdings_value = _position_totals(sim, student, live)
    cash = _money(student["cash_balance"] or 0)
    start = _money(student["start_cash"] or 0)
    total = _money(cash + holdings_value)
    totals.append((student["email"], total))
    valued.append((student, cash, holdings_value, total,
                   _money(total - start), ((total - start) / start) if start else 0.0))
  totals.sort(key=lambda t: t[1], reverse=True)
  ranks = {email: i + 1 for i, (email, _total) in enumerate(totals)}

  bench_price = _money(live[benchmark]["price"]) if live.get(benchmark) else None
  start = _benchmark_start(sim)
  if start is None:
    start = bench_price
  bench_return = ((bench_price - start) / start) if (bench_price and start) else 0.0
  try:
    mode = (market.market_status() or {}).get("mode", "UNKNOWN")
  except Exception:
    mode = "UNKNOWN"

  written = 0
  skipped = 0
  for student, cash, holdings_value, total, gain, rate in valued:
    # Pre-close runs refresh prices only; snapshots are end-of-day records.
    if not snapshot_due or student["email"] in already:
      skipped += 1
      continue
    app_tables.snapshots.add_row(
      simulation=sim,
      student=student,
      snapshot_at=now,
      date_str=date_str,
      cash=cash,
      holdings_value=holdings_value,
      total_value=total,
      gain_loss=gain,
      **{"return": rate},
      rank=ranks.get(student["email"]),
      benchmark_ticker=benchmark,
      benchmark_price=bench_price,
      benchmark_return=bench_return,
      market_data_status=str(mode),
    )
    written += 1
  return {"ok": True, "simcode": code, "date": date_str,
          "written": written, "skipped": skipped,
          "snapshot_due": snapshot_due,
          "students": len(students), "benchmark": benchmark,
          "benchmark_price": bench_price}

@anvil.server.background_task
def background_snapshot_all():
  sim_table = app_tables.simulations
  sim_rows = sim_table.search(open=True)
  for row in sim_rows:
    background_refresh_and_snapshot(row['simcode'])

@anvil.server.background_task
def background_refresh_and_snapshot(simcode, force_snapshot=False):
  """Background-task entry: same worker, no user session, no return."""
  try:
    result = refresh_and_snapshot(simcode, force_snapshot=force_snapshot)
    print("snapshot result: " + str(result))
  except Exception as error:
    print("snapshot failed for " + str(simcode) + ": " + str(error))


@anvil.server.callable
def launch_refresh_and_snapshot(simcode):
  """Teacher button: gate, then run the worker in the background.

  Passes force_snapshot=True so the button always writes, even pre-close.
  """
  sim, _teacher = _teacher_sim(str(simcode or "").strip().upper())
  anvil.server.launch_background_task(
    "background_refresh_and_snapshot", sim["simcode"], True)
  return {"ok": True, "simcode": sim["simcode"], "started": True}


@anvil.server.callable
def scheduled_refresh_all():
  """Scheduled-task entry (owner attaches in the editor as a plain
  interval): one background task per active+open sim. Skips sims with no
  active students. Nights and weekends are gated out here because the
  scheduler has no quiet hours. Market holidays still fire (they are
  weekdays) and will write one snapshot set on carried-over prices —
  harmless, but the teacher can delete those rows or leave them."""
  if not _in_run_window():
    return {"ok": True, "launched": [], "skipped": "outside run window"}
  launched = []
  for sim in app_tables.simulations.search(active=True, open=True):
    code = sim["simcode"]
    if not app_tables.student_data.search(simcode=code, active=True):
      continue
    anvil.server.launch_background_task("background_refresh_and_snapshot", code)
    launched.append(code)
  return {"ok": True, "launched": launched}


@anvil.server.callable
def recent_history(simcode, limit=90):
  """Caller's own snapshot series for the portal chart (oldest first)."""
  user = anvil.users.get_user()
  if user is None:
    return {"ok": False, "message": "Log in first."}
  email = str(user["email"] or "").strip().lower()
  code = str(simcode or "").strip().upper()
  student = app_tables.student_data.get(simcode=code, email=email, active=True)
  if student is None:
    return {"ok": False, "message": "No active account in this simulation."}
  try:
    limit = max(2, min(365, int(limit)))
  except (TypeError, ValueError):
    limit = 90
  rows = sorted(
    app_tables.snapshots.search(student=student),
    key=lambda r: (r["snapshot_at"] is None, r["snapshot_at"]),
  )[-limit:]
  points = []
  for row in rows:
    try:
      ret = float(row["return"] or 0)
    except (TypeError, ValueError):
      ret = 0.0
    try:
      bench = float(row["benchmark_return"] or 0)
    except (TypeError, ValueError):
      bench = 0.0
    points.append({
      "date": row["date_str"] or "",
      "returnRate": ret,
      "benchmarkReturn": bench,
    })
  return {"ok": True, "simcode": code, "points": points}


@anvil.server.callable
def benchmark_status(simcode):
  """Benchmark vs class comparison line for the portals.

  Any caller attached to the sim (teacher, or an active student) may read
  it: ticker, latest and first-recorded price, return since start, as-of
  date, plus the class average return across active students' latest
  snapshots. Empty until the first snapshot exists.
  """
  user = anvil.users.get_user()
  if user is None:
    return {"ok": False, "message": "Log in first."}
  email = str(user["email"] or "").strip().lower()
  code = str(simcode or "").strip().upper()
  sim = app_tables.simulations.get(simcode=code)
  if sim is None:
    return {"ok": False, "message": "Unknown simulation code."}
  is_teacher = str(sim["teacher_email"] or "").strip().lower() == email
  if not is_teacher and app_tables.student_data.get(
      simcode=code, email=email, active=True) is None:
    return {"ok": False, "message": "No active account in this simulation."}
  rows = list(app_tables.snapshots.search(simulation=sim))
  if not rows:
    return {"ok": False, "message": "No snapshots yet."}
  dated = sorted(
    (r for r in rows if r["snapshot_at"] is not None),
    key=lambda r: r["snapshot_at"],
  )
  if not dated:
    return {"ok": False, "message": "No snapshots yet."}
  first = dated[0]
  latest = dated[-1]
  try:
    start = float(first["benchmark_price"] or 0)
  except (TypeError, ValueError):
    start = 0.0
  try:
    price = float(latest["benchmark_price"] or 0)
  except (TypeError, ValueError):
    price = 0.0
  bench_return = ((price - start) / start) if (price > 0 and start > 0) else 0.0
  latest_per_student = {}
  for row in dated:
    student = row["student"]
    if student is not None:
      latest_per_student[student["email"]] = row
  class_returns = []
  for row in latest_per_student.values():
    try:
      class_returns.append(float(row["return"] or 0))
    except (TypeError, ValueError):
      continue
  class_avg = (sum(class_returns) / len(class_returns)) if class_returns else 0.0
  return {
    "ok": True,
    "simcode": code,
    "ticker": str(latest["benchmark_ticker"] or first["benchmark_ticker"] or "SPY"),
    "price": price,
    "startPrice": start,
    "return": bench_return,
    "asOf": latest["date_str"] or "",
    "classAvgReturn": class_avg,
    "studentCount": len(class_returns),
  }
