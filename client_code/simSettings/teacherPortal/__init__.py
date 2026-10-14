from ._anvil_designer import teacherPortalTemplate
from anvil import *
import anvil.server


def _money_text(value):
  try:
    number = float(value or 0)
  except (TypeError, ValueError):
    number = 0.0
  return "$" + format(number, ",.2f")


def _pct_text(value):
  try:
    number = float(value or 0)
  except (TypeError, ValueError):
    number = 0.0
  return "%.2f%%" % (number * 100.0)


def _fill_grid(grid, records):
  grid.clear()
  for record in records:
    grid.add_component(DataRowPanel(item=record))


class teacherPortal(teacherPortalTemplate):
  def __init__(self, simcode="", **properties):
    super().__init__(**properties)
    self.simcode = simcode or ""
    self._all_orders = []

    self.content_panel.add_component(Label(text="Teacher portal", font_size=20))
    self.sim_label = Label(text="")
    self.content_panel.add_component(self.sim_label)
    self.benchmark_label = Label(text="")
    self.content_panel.add_component(self.benchmark_label)

    self.content_panel.add_component(Label(text="Leaderboard", font_size=16))
    self.board_grid = DataGrid(columns=[
      {"id": "rank", "title": "Rank", "data_key": "rank"},
      {"id": "name", "title": "Student", "data_key": "name"},
      {"id": "class", "title": "Class", "data_key": "classPeriod"},
      {"id": "cash", "title": "Cash", "data_key": "cash"},
      {"id": "holdings", "title": "Holdings", "data_key": "holdings"},
      {"id": "total", "title": "Total", "data_key": "total"},
      {"id": "gain", "title": "Gain/loss", "data_key": "gain"},
      {"id": "ret", "title": "Return", "data_key": "ret"},
    ])
    self.content_panel.add_component(self.board_grid)

    self.content_panel.add_component(Label(text="All transactions", font_size=16))
    self.filter_dropdown = DropDown(items=[("All students", "")])
    self.filter_dropdown.add_event_handler("change", self.apply_filter)
    self.content_panel.add_component(self.filter_dropdown)
    self.tx_grid = DataGrid(columns=[
      {"id": "submitted", "title": "Submitted", "data_key": "submitted"},
      {"id": "student", "title": "Student", "data_key": "student"},
      {"id": "order", "title": "Order", "data_key": "order"},
      {"id": "price", "title": "Price", "data_key": "price"},
      {"id": "status", "title": "Status", "data_key": "status"},
      {"id": "message", "title": "Message", "data_key": "message"},
    ])
    self.content_panel.add_component(self.tx_grid)

    buttons = FlowPanel()
    self.refresh_button = Button(text="Refresh")
    self.refresh_button.add_event_handler("click", self.refresh)
    buttons.add_component(self.refresh_button)
    self.integrity_button = Button(text="Run integrity check")
    self.integrity_button.add_event_handler("click", self.run_integrity)
    buttons.add_component(self.integrity_button)
    self.diagnostics_button = Button(text="Run diagnostics")
    self.diagnostics_button.add_event_handler("click", self.run_diagnostics)
    buttons.add_component(self.diagnostics_button)
    self.snapshot_button = Button(text="Refresh + snapshot now")
    self.snapshot_button.add_event_handler("click", self.run_snapshot)
    buttons.add_component(self.snapshot_button)
    self.recover_button = Button(text="Recover interrupted orders")
    self.recover_button.add_event_handler("click", self.run_recover)
    buttons.add_component(self.recover_button)
    back = Link(text="Back to settings")
    back.add_event_handler(
      "click", lambda **e: open_form("simSettings", simcode=self.simcode))
    buttons.add_component(back)
    self.content_panel.add_component(buttons)

    self.results_box = TextArea(enabled=False)
    self.results_box.height = 220
    self.content_panel.add_component(self.results_box)

    self.refresh()

  def refresh(self, **event_args):
    try:
      board = anvil.server.call("teacher_leaderboard", self.simcode)
      tx = anvil.server.call("teacher_all_transactions", self.simcode)
    except Exception as error:
      self.sim_label.text = "Could not load: " + str(error)
      return
    if not board.get("ok"):
      self.sim_label.text = board.get("message", "Could not load.")
      return
    if not tx.get("ok"):
      self.sim_label.text = tx.get("message", "Could not load.")
      return
    self.sim_label.text = "Simulation %s · %d student(s) on the board." % (
      board.get("simcode", ""), len(board.get("board", [])))
    try:
      bench = anvil.server.call("benchmark_status", self.simcode)
    except Exception:
      bench = {"ok": False}
    if bench.get("ok"):
      self.benchmark_label.text = (
        "Benchmark %s %s (%s since start) · class average %s "
        "across %d student(s) · as of %s." % (
          bench["ticker"], _money_text(bench["price"]),
          _pct_text(bench["return"]), _pct_text(bench["classAvgReturn"]),
          bench["studentCount"], bench["asOf"] or "—"))
    else:
      self.benchmark_label.text = "Benchmark comparison appears after the first snapshot."
    _fill_grid(self.board_grid, [{
      "rank": row["rank"],
      "name": row["name"] or row["email"],
      "classPeriod": row["classPeriod"],
      "cash": _money_text(row["cash"]),
      "holdings": _money_text(row["holdings"]),
      "total": _money_text(row["total"]),
      "gain": _money_text(row["gain"]),
      "ret": _pct_text(row["return"]),
    } for row in board.get("board", [])])
    self._all_orders = tx.get("orders", [])
    students = sorted({(o["name"] or o["email"], o["email"]) for o in self._all_orders
                       if o["email"]})
    self.filter_dropdown.items = [("All students", "")] + [
      ("%s (%s)" % (name, email), email) for name, email in students
    ]
    self.filter_dropdown.selected_value = ""
    self.apply_filter()

  def apply_filter(self, **event_args):
    chosen = self.filter_dropdown.selected_value or ""
    orders = [o for o in self._all_orders if not chosen or o["email"] == chosen]
    _fill_grid(self.tx_grid, [{
      "submitted": o["submittedAt"],
      "student": o["name"] or o["email"],
      "order": " ".join([str(o["side"] or ""), str(o["shares"] or ""),
                         str(o["ticker"] or "")]).strip(),
      "price": _money_text(o["price"]),
      "status": o["status"],
      "message": o["message"],
    } for o in orders])

  def run_snapshot(self, **event_args):
    self.results_box.text = "Starting refresh + snapshot in the background…"
    try:
      result = anvil.server.call("launch_refresh_and_snapshot", self.simcode)
    except Exception as error:
      self.results_box.text = "Could not start the snapshot task: " + str(error)
      return
    self.results_box.text = (
      "Snapshot task started for %s. Re-run Refresh in a minute to see it "
      "in the history (background tasks report only in the app logs)."
      % result.get("simcode", ""))

  def run_recover(self, **event_args):
    self.results_box.text = "Recovering interrupted orders…"
    try:
      result = anvil.server.call("recover_prepared", self.simcode)
    except Exception as error:
      self.results_box.text = "Recovery failed: " + str(error)
      return
    lines = [result.get("summary", "")]
    for action in result.get("actions", []):
      lines.append("%s: %s — %s" % (
        action.get("orderId", "?"), action.get("action", "?"),
        action.get("detail", "")))
    self.results_box.text = "\n".join(lines)

  def run_integrity(self, **event_args):
    self.results_box.text = "Running integrity check…"
    try:
      result = anvil.server.call("integrity_check", self.simcode)
    except Exception as error:
      self.results_box.text = "Integrity check failed: " + str(error)
      return
    lines = [result.get("summary", "")]
    for issue in result.get("issues", []):
      lines.append("ISSUE: " + issue)
    for note in result.get("notes", []):
      lines.append("note: " + note)
    self.results_box.text = "\n".join(lines)

  def run_diagnostics(self, **event_args):
    self.results_box.text = "Running diagnostics…"
    try:
      result = anvil.server.call("diagnostics", self.simcode)
    except Exception as error:
      self.results_box.text = "Diagnostics failed: " + str(error)
      return
    lines = [result.get("summary", "")]
    for check in result.get("checks", []):
      lines.append("[%s] %s — %s" % (
        check.get("status", "?"), check.get("name", "?"), check.get("details", "")))
    self.results_box.text = "\n".join(lines)