from ._anvil_designer import debugPortalTemplate
from anvil import *
import uuid
import anvil.server


def _money(value):
  try:
    return "$%.2f" % float(value)
  except (TypeError, ValueError):
    return "—"


def _percent(value):
  try:
    return "%.2f%%" % (float(value) * 100)
  except (TypeError, ValueError):
    return "—"


def _fill_grid(grid, rows):
  """Populate a programmatic DataGrid. DataGrid has no .rows attribute;
  rows are DataRowPanels whose item keys match the columns' data_keys."""
  grid.clear()
  for record in rows:
    grid.add_component(DataRowPanel(item=record))


class debugPortal(debugPortalTemplate):
  def __init__(self, simcode="", **properties):
    super().__init__(**properties)
    self.simcode = simcode or ""
    self.target = ""
    self.holdings_grid = DataGrid(auto_header=True, rows_per_page=50)
    self.holdings_grid.columns = [
      {"id": "ticker", "title": "Ticker", "data_key": "ticker"},
      {"id": "shares", "title": "Shares", "data_key": "shares"},
      {"id": "avgCost", "title": "Avg. cost", "data_key": "avgCost"},
      {"id": "price", "title": "Price", "data_key": "price"},
      {"id": "marketValue", "title": "Market value", "data_key": "marketValue"},
      {"id": "unrealizedGain", "title": "Gain/loss", "data_key": "unrealizedGain"},
      {"id": "returnRate", "title": "Return", "data_key": "returnRate"},
    ]
    self.outlined_card_7.add_component(self.holdings_grid)
    self.orders_grid = DataGrid(auto_header=True, rows_per_page=10)
    self.orders_grid.columns = [
      {"id": "submittedAt", "title": "Submitted", "data_key": "submittedAt"},
      {"id": "status", "title": "Status", "data_key": "status"},
      {"id": "summary", "title": "Order", "data_key": "summary"},
      {"id": "price", "title": "Price", "data_key": "price"},
      {"id": "message", "title": "Message", "data_key": "message"},
    ]
    self.outlined_card_8.add_component(self.orders_grid)
    self.history_note = Label(
      text="Daily history will appear after snapshots (later milestone).")
    self.outlined_card_10.add_component(self.history_note)
    self.back_link = Link(text="Back to settings")
    self.back_link.add_event_handler(
      "click", lambda **e: open_form("simSettings", simcode=self.simcode))
    self.navbar_links.add_component(self.back_link)
    self.userSelectDropdown.add_event_handler("change", self.load_selected)
    self.applyButton.add_event_handler("click", self.load_selected)
    self.createNewUserButton.add_event_handler("click", self.create_debug_user)
    self.submitButton.add_event_handler("click", self.submit_as_target)
    self.tickerBox.add_event_handler("lost_focus", self.preview_ticker)
    self.tickerPill.message = "DEBUG PORTAL — teacher only, LIVE DATA."
    self.tickerPill.level = "warning"
    self.refresh_roster(select_first=True)

  def preview_ticker(self, **event_args):
    ticker = str(self.tickerBox.text or "").strip()
    if not ticker:
      self.tickerPill.message = "DEBUG PORTAL — teacher only, LIVE DATA."
      self.tickerPill.level = "warning"
      return
    try:
      quote = anvil.server.call("preview_quote", self.simcode, ticker)
    except Exception:
      self.tickerPill.message = "DEBUG · price unavailable right now."
      self.tickerPill.level = "warning"
      return
    if not quote.get("ok"):
      self.tickerPill.message = "DEBUG · " + quote.get("message", "Price unavailable.")
      self.tickerPill.level = "warning"
      return
    source = str(quote.get("source", ""))
    label = "LIVE" if source.startswith("ALPACA") else (source or "PRICE")
    self.tickerPill.message = "DEBUG · %s · %s (%s)" % (
      quote["ticker"], _money(quote["price"]), label)
    self.tickerPill.level = "info"

  def refresh_roster(self, select_first=False, select_email=""):
    try:
      panel = anvil.server.call("teacher_panel", self.simcode)
    except Exception as error:
      alert("The debug portal could not load: " + str(error))
      return
    if not panel.get("ok"):
      alert(panel.get("message", "The debug portal could not load."))
      return
    roster = panel.get("roster", [])
    self.userSelectDropdown.items = [
      ("%s · %s · %s%s" % (
        account["name"] or account["email"], account["classPeriod"],
        account["email"], "" if account["active"] else " [inactive]"),
       account["email"])
      for account in roster
    ]
    if select_email:
      self.target = select_email
    elif select_first and roster:
      self.target = roster[0]["email"]
    self.userSelectDropdown.selected_value = self.target or None
    self.load_selected()

  def load_selected(self, **event_args):
    self.target = self.userSelectDropdown.selected_value or ""
    if not self.target:
      return
    try:
      data = anvil.server.call("debug_portfolio_as", self.simcode, self.target)
    except Exception as error:
      alert("Could not load account: " + str(error))
      return
    if not data.get("ok"):
      alert(data.get("message", "Could not load account."))
      return
    student = data["student"]
    self.valueBox.text = _money(student["totalValue"])
    self.cashBox.text = _money(student["cashBalance"])
    self.holdingsBox.text = _money(student["holdingsValue"])
    self.returnBox.text = _percent(student["returnRate"])
    self.gainLossBox.text = _money(student["dollarGainLoss"])
    self.rankBox.text = ("#%d of %d" % (student["rank"], student["rankOf"])
                         if student["rank"] else "—")
    _fill_grid(self.holdings_grid, [{
      "ticker": holding["ticker"],
      "shares": holding["shares"],
      "avgCost": _money(holding["avgCost"]),
      "price": _money(holding["price"]),
      "marketValue": _money(holding["marketValue"]),
      "unrealizedGain": _money(holding["unrealizedGain"]),
      "returnRate": _percent(holding["returnRate"]),
    } for holding in data["holdings"]])
    try:
      orders = anvil.server.call("debug_recent_as", self.simcode, self.target)
    except Exception:
      orders = {"ok": False}
    if orders.get("ok"):
      _fill_grid(self.orders_grid, [{
        "submittedAt": o["submittedAt"],
        "status": o["status"],
        "summary": " ".join(str(part) for part in
                            [o["side"], o["shares"], o["ticker"]] if part),
        "price": _money(o["price"]) if o["price"] != "" else "—",
        "message": o["message"],
      } for o in orders["orders"]])
    self.submitButton.enabled = bool(data.get("tradingOpen"))
    self.submitButton.text = ("Submit Order" if data.get("tradingOpen")
                              else "Trading is closed")

  def create_debug_user(self, **event_args):
    email = str(self.testUserEmail.text or "").strip()
    if not email:
      alert("Enter an email for the new debug account.")
      return
    try:
      result = anvil.server.call("debug_create_student", self.simcode, email)
    except Exception as error:
      alert("Could not create account: " + str(error))
      return
    if not result.get("ok"):
      alert(result.get("message", "Could not create account."))
      return
    self.testUserEmail.text = ""
    self.refresh_roster(select_email=result["email"])

  def submit_as_target(self, **event_args):
    if not self.target:
      alert("Select a debug account first.")
      return
    try:
      shares = int(str(self.sharesBox.text or "").strip())
    except ValueError:
      alert("Shares must be a whole number greater than zero.")
      return
    if shares <= 0:
      alert("Shares must be a whole number greater than zero.")
      return
    ticker = str(self.tickerBox.text or "").strip().upper()
    side = self.orderType.selected_value or "BUY"
    if not ticker:
      alert("Enter a ticker symbol.")
      return
    confirmed = alert(
      "Submit %s %d share(s) of %s as %s?" % (side, shares, ticker, self.target),
      buttons=[("Yes, submit", True), ("Cancel", False)])
    if not confirmed:
      return
    request_id = "DEBUG:" + uuid.uuid4().hex
    self.submitButton.enabled = False
    try:
      result = anvil.server.call(
        "debug_place_order", self.simcode, self.target, side, ticker,
        shares, str(self.noteBox.text or ""), request_id)
    except Exception as error:
      alert("Order outcome needs verification: " + str(error))
      self.load_selected()
      return
    alert(("%s Order ID: %s." % (result.get("message", ""), result.get("orderId", "")))
          if result.get("orderId") else result.get("message", "No message."))
    if result.get("ok"):
      self.tickerBox.text = ""
      self.sharesBox.text = ""
      self.noteBox.text = ""
    self.load_selected()
