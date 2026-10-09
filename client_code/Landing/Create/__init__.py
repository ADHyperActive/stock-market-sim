from ._anvil_designer import CreateTemplate
from anvil import *
import anvil.server


class Create(CreateTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)
    self.heading = Label(text="Create a new simulation", font_size=20)
    self.explainer = Label(
      text="You will become this simulation's teacher. Students join with the code shown after creation."
    )
    self.create_button = Button(text="Create simulation", role="primary-color")
    self.create_button.add_event_handler("click", self.do_create)
    self.result = Label(text="")
    self.back_button = Link(text="Back")
    self.back_button.add_event_handler("click", lambda **e: open_form("Landing"))
    for component in (self.heading, self.explainer, self.create_button, self.result, self.back_button):
      self.content_panel.add_component(component)

  def do_create(self, **event_args):
    self.create_button.enabled = False
    self.result.text = "Creating…"
    try:
      outcome = anvil.server.call("create_simulation")
    except Exception as error:
      self.result.text = "Could not create the simulation: " + str(error)
      self.create_button.enabled = True
      return
    self.result.text = "Simulation code: " + outcome["simcode"]
    self.create_button.enabled = True
