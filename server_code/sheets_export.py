"""Export simulation transactions to a Google Sheet for Gemini/Sheets analysis.

Path (a): the app creates a Google Sheet containing the journal rows and
returns the link. The teacher opens it in Gemini (Sheets extension) and asks
questions there. No LLM API key lives in this app, and no student data is
sent to any model from here.

Every callable is teacher-gated via teachers._teacher_sim. Google OAuth uses
Anvil's Google service: the client calls anvil.google.auth.login() once, then
server code calls anvil.google.auth.get_user_access_token() for each API call.
The token is never stored and never logged.

Required Google service scopes (set in the Anvil editor, Google service):
  https://www.googleapis.com/auth/drive.file
  https://www.googleapis.com/auth/spreadsheets
drive.file is deliberate: the app can only touch sheets it created — it cannot
read any other file in the teacher's Drive.
"""

import anvil.server
import anvil.google.auth
import anvil.http
from anvil.tables import app_tables
from teachers import _teacher_sim

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

COLUMNS = [
    "Order ID", "Submitted At", "Executed At", "Student", "Side",
    "Shares", "Price", "Gross Amount", "Commission", "Cash After",
    "Shares After", "Gain/Loss", "Status", "Message", "Channel",
    "Market State", "Data Source", "Classification", "Request ID",
    "Quote Time", "Quote Age (sec)", "Order Note", "Prior Close",
    "Cash Before", "Shares Before", "Avg Cost Before", "Avg Cost After",
    "Existed Before",
]


def _col_letter(n):
  """1-based column number -> A, B, ... Z, AA, AB, ..."""
  letters = ""
  while n > 0:
    n, rem = divmod(n - 1, 26)
    letters = chr(65 + rem) + letters
  return letters


def _google_token():
  """Access token for the current Google user, or a clear error."""
  try:
    return anvil.google.auth.get_user_access_token()
  except Exception as error:
    raise PermissionError(
        "Google is not connected. Open the sheet-export section and click "
        "'Connect Google' first.")


def _api(method, url, token, payload=None):
  headers = {
      "Authorization": "Bearer " + token,
      "Accept": "application/json",
  }
  return anvil.http.request(url, method=method, headers=headers, json=payload)


def _iso(value):
  if value is None:
    return ""
  try:
    return value.isoformat()
  except AttributeError:
    return str(value or "")


def _money_cell(value):
  if value is None:
    return ""
  try:
    return float(value)
  except (TypeError, ValueError):
    return value


@anvil.server.callable
def export_transactions(simcode, anonymize=True):
  """Create a Google Sheet with this sim's transactions and return its URL.

  Teacher only. anonymize=True replaces names/emails with Student N labels.
  """
  sim, teacher_email = _teacher_sim(simcode)
  token = _google_token()

  code = sim["simcode"]
  rows = app_tables.transactions.search(simulation=sim)
  ordered = sorted(rows, key=lambda r: r["submitted_at"] or _now_min(), reverse=True)

  student_map = {}
  next_student = [1]

  def student_label(student):
    if student is None:
      return "—"
    email = str(student["email"] or "").strip()
    if anonymize:
      if email not in student_map:
        student_map[email] = "Student %d" % next_student[0]
        next_student[0] += 1
      return student_map[email]
    name = ((str(student["first_name"] or "") + " " +
             str(student["last_name"] or "")).strip() or email)
    return name + " <" + email + ">"

  values = [COLUMNS]
  for r in ordered:
    submitted = r["submitted_at"]
    executed = r["executed_at"]
    values.append([
        r["order_id"] or "",
        _iso(submitted),
        _iso(executed),
        student_label(r["student"]),
        r["side"] or "",
        r["shares"] if r["shares"] is not None else "",
        _money_cell(r["execution_price"]),
        _money_cell(r["gross_amount"]),
        _money_cell(r["commission"]),
        _money_cell(r["cash_after"]),
        r["shares_after"] if r["shares_after"] is not None else "",
        _money_cell(r["gain_loss"]),
        r["status"] or "",
        str(r["message"] or "")[:500],
        r["channel"] or "",
        r["market_state"] or "",
        r["data_source"] or "",
        r["price_classification"] or "",
        r["request_id"] or "",
        _iso(r["quote_time"]),
        r["quote_age_sec"] if r["quote_age_sec"] is not None else "",
        r["order_note"] or "",
        r["prior_close"] or "",
        _money_cell(r["cash_before"]),
        r["shares_before"] if r["shares_before"] is not None else "",
        _money_cell(r["avg_cost_before"]),
        _money_cell(r["avg_cost_after"]),
        "YES" if r["existed_before"] else "NO",
    ])

  # Create the spreadsheet in the teacher's Drive.
  file_metadata = {
      "name": "Stock Market Sim — %s — Transactions" % code,
      "mimeType": "application/vnd.google-apps.spreadsheet",
  }
  resp = _api("POST", "https://www.googleapis.com/drive/v3/files", token,
              payload=file_metadata)
  file_id = resp["id"]
  file_url = "https://docs.google.com/spreadsheets/d/" + file_id

  # Populate the sheet.
  end_col = _col_letter(len(COLUMNS))
  range_name = "A1:%s%d" % (end_col, len(values))
  _api("PUT",
       "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s?valueInputOption=RAW"
       % (file_id, range_name),
       token, payload={"values": values})

  return {
      "ok": True,
      "url": file_url,
      "fileId": file_id,
      "rowCount": len(values) - 1,
      "anonymized": bool(anonymize),
      "teacherEmail": teacher_email,
  }


def _now_min():
  """Fallback timestamp for sorting when submitted_at is None."""
  import datetime
  return datetime.datetime.min
