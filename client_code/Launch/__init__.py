from ._anvil_designer import LaunchTemplate
from anvil import *
import anvil.google.auth, anvil.google.drive
from anvil.google.drive import app_files
import anvil.users
import anvil.server


class Launch(LaunchTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.createButton.add_event_handler("click", self.open_create)
    self.loadButton.add_event_handler("click", self.open_join)
    self.sims_heading = Label(text="My simulations", font_size=18, visible=False)
    self.sims_list = ColumnPanel(visible=False)
    self.content_panel.add_component(self.sims_heading)
    self.content_panel.add_component(self.sims_list)
    self.refresh_login_state()
    self.refresh_sims()

  def refresh_login_state(self):
    self.navbar_links.clear()
    try:
      me = anvil.server.call("auth_me")
    except Exception:
      me = {"logged_in": False, "email": ""}
    if me.get("logged_in"):
      user_label = Label(text=me.get("email", ""))
      logout_button = Link(text="Log out")
      logout_button.add_event_handler("click", self.do_logout)
      self.navbar_links.add_component(user_label)
      self.navbar_links.add_component(logout_button)
    else:
      login_button = Link(text="Log in / Sign up")
      login_button.add_event_handler("click", self.do_login)
      self.navbar_links.add_component(login_button)

  def do_login(self, **event_args):
    anvil.users.login_with_form(allow_cancel=True)
    self.refresh_login_state()
    self.refresh_sims()

  def do_logout(self, **event_args):
    anvil.users.logout()
    self.refresh_login_state()
    self.refresh_sims()

  def refresh_sims(self):
    """List simulations the caller can re-open (owned + joined)."""
    self.sims_list.clear()
    self.sims_heading.visible = False
    self.sims_list.visible = False
    try:
      result = anvil.server.call("my_simulations")
    except Exception:
      return
    if not result.get("logged_in"):
      return
    owned = result.get("owned", [])
    joined = result.get("joined", [])
    if not owned and not joined:
      return
    self.sims_heading.visible = True
    self.sims_list.visible = True
    for sim in owned:
      state = "Trading open" if sim.get("open") else "Trading closed"
      self._sim_row("Teacher · %s · %s" % (sim.get("simcode", ""), state),
                    "Open teacher panel", "simSettings", sim.get("simcode", ""))
    joined_codes = {s.get("simcode") for s in owned}
    for sim in joined:
      if sim.get("simcode") in joined_codes:
        continue
      state = "Trading open" if sim.get("open") else "Trading closed"
      self._sim_row("Student · %s · %s" % (sim.get("simcode", ""), state),
                    "Open portfolio", "studentPortal", sim.get("simcode", ""))

  def _sim_row(self, text, button_text, form, simcode):
    row = FlowPanel()
    row.add_component(Label(text=text))
    button = Link(text=button_text)
    button.add_event_handler(
      "click", lambda f=form, c=simcode, **e: open_form(f, simcode=c))
    row.add_component(button)
    self.sims_list.add_component(row)

  def open_create(self, **event_args):
    if not anvil.users.get_user():
      anvil.users.login_with_form(allow_cancel=True)
      self.refresh_login_state()
    if anvil.users.get_user():
      open_form("Launch.Create")

  def open_join(self, **event_args):
    if not anvil.users.get_user():
      anvil.users.login_with_form(allow_cancel=True)
      self.refresh_login_state()
    if anvil.users.get_user():
      open_form("Launch.Join")
