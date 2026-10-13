from ._anvil_designer import simSettingsTemplate
from anvil import *
import anvil.server


DEFAULTS = {
  "commission": 0.0,
  "quoteRefreshMinutes": 1,
  "quoteFreshnessSeconds": 90,
  "delayedSipMaxAgeSeconds": 1200,
  "benchmarkTicker": "SPY",
}


class simSettings(simSettingsTemplate):
  def __init__(self, simcode="", **properties):
    super().__init__(**properties)
    self.simcode = simcode or ""
    self.status = Label(text="")
    self.content_panel.add_component(self.status)
    self.back_link = Link(text="Back to home")
    self.back_link.add_event_handler("click", lambda **e: open_form("Launch"))
    self.navbar_links.add_component(self.back_link)
    self.applyButton.add_event_handler("click", self.apply_settings)
    self.debugButton.add_event_handler(
      "click", lambda **e: open_form("debugPortal", simcode=self.simcode))
    self.load_settings()

  def load_settings(self):
    try:
      panel = anvil.server.call("teacher_panel", self.simcode)
    except Exception as error:
      self.status.text = "Could not load settings: " + str(error)
      return
    if not panel.get("ok"):
      self.status.text = panel.get("message", "Could not load settings.")
      return
    config = panel.get("config", {})
    self.tradingOpen.checked = bool(panel.get("open"))
    self.commissionPerTrade.text = str(config.get("commission", DEFAULTS["commission"]))
    self.quoteRefreshMinutes.text = str(config.get("quoteRefreshMinutes", DEFAULTS["quoteRefreshMinutes"]))
    self.quoteFreshnessSeconds.text = str(
      config.get("quoteFreshnessSeconds", DEFAULTS["quoteFreshnessSeconds"]))
    self.delayedSipMaxAgeSeconds.text = str(
      config.get("delayedSipMaxAgeSeconds", DEFAULTS["delayedSipMaxAgeSeconds"]))
    self.benchmarkTicker.text = str(config.get("benchmarkTicker", DEFAULTS["benchmarkTicker"]))
    counts = panel.get("counts", {})
    self.status.text = (
      "Simulation %s · %d student(s) · %d holding(s) · %d transaction(s)." % (
        panel.get("simcode", ""), counts.get("students", 0),
        counts.get("holdings", 0), counts.get("transactions", 0)))

  def apply_settings(self, **event_args):
    try:
      patch = {
        "open": bool(self.tradingOpen.checked),
        "commission": float(str(self.commissionPerTrade.text or "0").strip()),
        "quoteRefreshMinutes": int(str(self.quoteRefreshMinutes.text or "1").strip()),
        "quoteFreshnessSeconds": int(str(self.quoteFreshnessSeconds.text or "90").strip()),
        "delayedSipMaxAgeSeconds": int(str(self.delayedSipMaxAgeSeconds.text or "1200").strip()),
        "benchmarkTicker": str(self.benchmarkTicker.text or "SPY").strip(),
      }
    except ValueError:
      alert("Settings must be numbers (except the benchmark ticker).")
      return
    try:
      result = anvil.server.call("teacher_set_settings", self.simcode, patch)
    except Exception as error:
      alert("Could not save settings: " + str(error))
      return
    if not result.get("ok"):
      alert(result.get("message", "Could not save settings."))
      return
    alert("Settings saved.")
    self.load_settings()
