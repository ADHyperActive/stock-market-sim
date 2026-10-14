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


def _fill_grid(grid, rows):
  """Populate a programmatic DataGrid. DataGrid has no .rows attribute;
  rows are DataRowPanels whose item keys match the columns' data_keys."""
  grid.clear()
  for record in rows:
    grid.add_component(DataRowPanel(item=record))


class studentPortal(studentPortalTemplate):
  def __init__(self, simcode="", **properties):
    super().__init__(**properties)
    self.simcode = simcode or ""
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
    self.history_note = Label(text="Loading history…")
    self.outlined_card_10.add_component(self.history_note)
    self.benchmark_note = Label(text="Loading benchmark…")
    self.outlined_card_10.add_component(self.benchmark_note)
    self.back_link = Link(text="Back to home")
    self.back_link.add_event_handler("click", lambda **e: open_form("Launch"))
    self.navbar_links.add_component(self.back_link)
    self.submitButton.add_event_handler("click", self.submit_order)
    self.tickerBox.add_event_handler("lost_focus", self.preview_ticker)
    self.refresh()

  def preview_ticker(self, **event_args):
    ticker = str(self.tickerBox.text or "").strip()
    if not ticker:
      self.tickerPill.message = "Enter any U.S. stock or ETF ticker."
      self.tickerPill.level = "info"
      return
    try:
      quote = anvil.server.call("preview_quote", self.simcode, ticker)
    except Exception:
      self.tickerPill.message = "Price unavailable right now."
      self.tickerPill.level = "warning"
      return
    if not quote.get("ok"):
      self.tickerPill.message = quote.get("message", "Price unavailable.")
      self.tickerPill.level = "warning"
      return
    source = str(quote.get("source", ""))
    classification = str(quote.get("classification", ""))
    if classification.startswith("STALE"):
      self.tickerPill.message = "%s · %s (STALE DATA — older cached price)" % (
        quote["ticker"], _money(quote["price"]))
      self.tickerPill.level = "warning"
      return
    label = "LIVE" if source.startswith("ALPACA") else (source or "PRICE")
    self.tickerPill.message = "%s · %s (%s)" % (
      quote["ticker"], _money(quote["price"]), label)
    self.tickerPill.level = "info"

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
    mode = (data.get("marketStatus") or {}).get("mode", "")
    if mode == "PRE_MARKET":
      self.tickerPill.message = "Pre-market session: BUY uses the ask, SELL uses the bid."
      self.tickerPill.level = "info"
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
    _fill_grid(self.holdings_grid, rows)
    try:
      orders = anvil.server.call("recent_orders", self.simcode)
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
    try:
      history = anvil.server.call("recent_history", self.simcode)
    except Exception:
      history = {"ok": False}
    points = history.get("points", []) if history.get("ok") else []
    if len(points) < 2:
      self.history_note.text = ("Daily history will appear after at least "
                                "two snapshots.")
    else:
      latest = points[-1]
      self.history_note.text = (
        "%d snapshot(s) · latest %s · return %s · benchmark %s." % (
          len(points), latest["date"], _percent(latest["returnRate"]),
          _percent(latest["benchmarkReturn"])))
    self._draw_history(points)

  def _draw_history(self, points):
    """Return-over-time chart (Plotly) or a text fallback.

    The Plotly import is lazy and guarded: if the component library is
    unavailable the portal keeps working with the textual summary above.
    """
    if getattr(self, "_chart", None) is None:
      self._chart = None
    if len(points) < 2:
      if self._chart is not None:
        self._chart.visible = False
      return
    try:
      import plotly.graph_objects as go
      from anvil import Plot
    except Exception:
      return
    dates = [p["date"] or "" for p in points]
    portfolio = [float(p["returnRate"] or 0) * 100 for p in points]
    benchmark = [float(p["benchmarkReturn"] or 0) * 100 for p in points]
    if self._chart is None:
      self._chart = Plot(height=260)
      self.outlined_card_10.add_component(self._chart)
    self._chart.visible = True
    self._chart.data = [
      go.Scatter(x=dates, y=portfolio, mode="lines+markers",
                 name="Your portfolio"),
      go.Scatter(x=dates, y=benchmark, mode="lines+markers",
                 name="Benchmark"),
    ]
    self._chart.layout = {
      "title": "Return over time (%)",
      "xaxis": {"title": "Date"},
      "yaxis": {"title": "Return (%)"},
      "margin": {"l": 50, "r": 20, "t": 40, "b": 50},
    }
    try:
      bench = anvil.server.call("benchmark_status", self.simcode)
    except Exception:
      bench = {"ok": False}
    if bench.get("ok"):
      self.benchmark_note.text = (
        "Benchmark %s %s (%s since start) · class average %s · as of %s." % (
          bench["ticker"], _money(bench["price"]), _percent(bench["return"]),
          _percent(bench["classAvgReturn"]), bench["asOf"] or "—"))
    else:
      self.benchmark_note.text = "Benchmark comparison appears after the first snapshot."

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
