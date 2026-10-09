from ._anvil_designer import JoinTemplate
from anvil import *
import anvil.server


class Join(JoinTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.heading = Label(text="Join a simulation", font_size=20)
    self.code_box = TextBox(placeholder="Simulation code, e.g. KQ7X2P")
    self.first_box = TextBox(placeholder="First name")
    self.last_box = TextBox(placeholder="Last name")
    self.period_box = TextBox(placeholder="Class period")
    self.join_button = Button(text="Join and open portfolio", role="primary-color")
    self.join_button.add_event_handler("click", self.do_join)
    self.message = Label(text="")
    self.back_button = Link(text="Back")
    self.back_button.add_event_handler("click", lambda **e: open_form("Landing"))
    for component in (
      self.heading, self.code_box, self.first_box,
      self.last_box, self.period_box, self.join_button,
      self.message, self.back_button,
    ):
      self.content_panel.add_component(component)

  def do_join(self, **event_args):
    self.join_button.enabled = False
    self.message.text = "Joining…"
    try:
      outcome = anvil.server.call(
        "join_simulation",
        self.code_box.text,
        self.first_box.text,
        self.last_box.text,
        self.period_box.text,
      )
    except Exception as error:
      self.message.text = "Could not join: " + str(error)
      self.join_button.enabled = True
      return
    self.join_button.enabled = True
    if not outcome.get("ok"):
      self.message.text = outcome.get("message", "Could not join.")
      return
    open_form("studentPortal", simcode=outcome["simcode"])
