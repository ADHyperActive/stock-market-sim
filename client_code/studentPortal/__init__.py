from ._anvil_designer import studentPortalTemplate
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


class studentPortal(studentPortalTemplate):
  def __init__(self, simcode="", **properties):
    super().__init__(**properties)
    self.simcode = simcode or ""
    self.holdings_grid = DataGrid(auto_header=True, rows_per_page=50)
    self.holdings_grid.columns = [
      {"id": "ticker", "title": "Ticker"},
      {"id": "shares", "title": "Shares"},
      {"id": "avgCost", "title": "Avg. cost"},
      {"id": "price", "title": "Price"},
      {"id": "marketValue", "title": "Market value"},
      {"id": "unrealizedGain", "title": "Gain/loss"},
      {"id": "returnRate", "title": "Return"},
    ]
    self.outlined_card_7.add_component(self.holdings_grid)
    self.orders_grid = DataGrid(auto_header=True, rows_per_page=10)
    self.orders_grid.columns = [
      {"id": "submittedAt", "title": "Submitted"},
      {"id": "status", "title": "Status"},
      {"id": "summary", "title": "Order"},
      {"id": "price", "title": "Price"},
      {"id": "message", "title": "Message"},
    ]
    self.outlined_card_8.add_component(self.orders_grid)
    self.history_note = Label(
      text="Daily history will appear after snapshots (later milestone).")
    self.outlined_card_10.add_component(self.history_note)
    self.back_link = Link(text="Back to home")
    self.back_link.add_event_handler("click", lambda **e: open_form("Launch"))
    self.navbar_links.add_component(self.back_link)
    self.submitButton.add_event_handler("click", self.submit_order)
    self.refresh()

  def refresh(self):
    try:
      data = anvil.server.call("get_portfolio", self.simcode)
    except Exception as error:
      alert("The portal could not load: " + str(error))
      return
    if not data.get("ok"):
      alert(data.get("message", "The portal could not load."))
      return
    student = data["student"]
    self.valueBox.text = _money(student["totalValue"])
    self.cashBox.text = _money(student["cashBalance"])
    self.holdingsBox.text = _money(student["holdingsValue"])
    self.returnBox.text = _percent(student["returnRate"])
    self.gainLossBox.text = _money(student["dollarGainLoss"])
    self.rankBox.text = ("#%d of %d" % (student["rank"], student["rankOf"])
                         if student["rank"] else "—")
    if data.get("testData"):
      self.tickerPill.message = "TEST DATA — stub prices, not market data."
      self.tickerPill.level = "warning"
    rows = []
    for holding in data["holdings"]:
      rows.append({
        "ticker": holding["ticker"],
        "shares": holding["shares"],
        "avgCost": _money(holding["avgCost"]),
        "price": _money(holding["price"]),
        "marketValue": _money(holding["marketValue"]),
        "unrealizedGain": _money(holding["unrealizedGain"]),
        "returnRate": _percent(holding["returnRate"]),
      })
    self.holdings_grid.rows = rows
    try:
      orders = anvil.server.call("recent_orders", self.simcode)
    except Exception:
      orders = {"ok": False}
    if orders.get("ok"):
      self.orders_grid.rows = [{
        "submittedAt": o["submittedAt"],
        "status": o["status"],
        "summary": " ".join(str(part) for part in
                            [o["side"], o["shares"], o["ticker"]] if part),
        "price": _money(o["price"]) if o["price"] != "" else "—",
        "message": o["message"],
      } for o in orders["orders"]]
    self.submitButton.enabled = bool(data.get("tradingOpen"))
    self.submitButton.text = ("Submit Order" if data.get("tradingOpen")
                              else "Trading is closed")

  def submit_order(self, **event_args):
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
      "Submit %s %d share(s) of %s?" % (side, shares, ticker),
      buttons=[("Yes, submit", True), ("Cancel", False)])
    if not confirmed:
      return
    request_id = "ANVIL:" + uuid.uuid4().hex
    self.submitButton.enabled = False
    try:
      result = anvil.server.call(
        "place_order", self.simcode, side, ticker, shares,
        str(self.noteBox.text or ""), request_id)
    except Exception as error:
      alert("Order outcome needs verification: " + str(error))
      self.refresh()
      return
    alert(("%s Order ID: %s." % (result.get("message", ""), result.get("orderId", "")))
          if result.get("orderId") else result.get("message", "No message."))
    if result.get("ok"):
      self.tickerBox.text = ""
      self.sharesBox.text = ""
      self.noteBox.text = ""
    self.refresh()
