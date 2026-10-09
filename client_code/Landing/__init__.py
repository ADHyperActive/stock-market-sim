from ._anvil_designer import LandingTemplate
from anvil import *
import anvil.server
import anvil.users
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables


class Landing(LandingTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    super().__init__(**properties)

    # Any code you write here will run before the form opens.

  @handle("logIn", "click")
  def logIn_click(self, **event_args):
    """This method is called when the button is clicked"""
    anvil.users.login_with_form()

  @handle("signUp", "click")
  def signUp_click(self, **event_args):
    """This method is called when the button is clicked"""
    anvil.users.login_with_form()
    
