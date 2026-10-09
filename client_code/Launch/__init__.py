from ._anvil_designer import LaunchTemplate
from anvil import *
import anvil.users
import anvil.server


class Launch(LaunchTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.createButton.add_event_handler("click", self.open_create)
    self.loadButton.add_event_handler("click", self.open_join)
    self.refresh_login_state()

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

  def do_logout(self, **event_args):
    anvil.users.logout()
    self.refresh_login_state()

  def open_create(self, **event_args):
    if not anvil.users.get_user():
      anvil.users.login_with_form(allow_cancel=True)
      self.refresh_login_state()
    if anvil.users.get_user():
      open_form("Landing.Create")

  def open_join(self, **event_args):
    if not anvil.users.get_user():
      anvil.users.login_with_form(allow_cancel=True)
      self.refresh_login_state()
    if anvil.users.get_user():
      open_form("Landing.Join")
