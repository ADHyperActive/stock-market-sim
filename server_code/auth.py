import anvil.secrets
"""Simulation accounts: login identity plus per-simulation roles.

Identity always comes from the Anvil Users service (passwords are hashed by
the platform; this app never sees or stores them). Every callable below
derives the caller from anvil.users.get_user() server-side and never trusts
a client-supplied email address.

Requires the Users service to be enabled on this app.
"""

import random
import string

import anvil.server
import anvil.users
from anvil.tables import app_tables

STARTING_CASH = 10000
SIMCODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SIMCODE_LENGTH = 6
MAX_NAME_LENGTH = 80
MAX_PERIOD_LENGTH = 20


def _require_email():
  """Logged-in user's email, normalised. Raises if nobody is logged in."""
  user = anvil.users.get_user()
  if user is None:
    raise anvil.server.PermissionDenied("Log in first.")
  email = str(user["email"] or "").strip().lower()
  if not email:
    raise anvil.server.PermissionDenied("Your login has no email address.")
  return email


def _clean_code(value):
  return str(value or "").strip().upper()


def _new_simcode():
  for _attempt in range(20):
    code = "".join(random.choice(SIMCODE_ALPHABET) for _ in range(SIMCODE_LENGTH))
    if not app_tables.simulations.get(simcode=code):
      return code
  raise RuntimeError("Could not generate a unique simulation code.")


def _role_for(email, code):
  sim = app_tables.simulations.get(simcode=code)
  if sim is None:
    return None
  if str(sim["teacher_email"] or "").strip().lower() == email:
    return "teacher"
  if app_tables.student_data.get(simcode=code, email=email) is not None:
    return "student"
  return None


@anvil.server.callable
def auth_me():
  """Who is calling, without raising. Used to render login/logout state."""
  user = anvil.users.get_user()
  if user is None:
    return {"logged_in": False, "email": ""}
  return {"logged_in": True, "email": str(user["email"] or "").strip().lower()}


@anvil.server.callable
def create_simulation():
  """Create a simulation owned by the caller. Caller becomes the teacher."""
  email = _require_email()
  code = _new_simcode()
  app_tables.simulations.add_row(
    teacher_email=email,
    simcode=code,
    active=True,
    open=False,
    config={},
  )
  return {"ok": True, "simcode": code}


@anvil.server.callable
def join_simulation(simcode, first_name, last_name, class_period):
  """Join a simulation as a student. Idempotent: re-joining returns existing."""
  email = _require_email()
  code = _clean_code(simcode)
  sim = app_tables.simulations.get(simcode=code, active=True)
  if sim is None:
    return {"ok": False, "message": "No active simulation uses that code."}
  existing = app_tables.student_data.get(simcode=code, email=email)
  if existing is not None:
    return {"ok": True, "simcode": code, "existing": True}
  first = str(first_name or "").strip()[:MAX_NAME_LENGTH]
  last = str(last_name or "").strip()[:MAX_NAME_LENGTH]
  period = str(class_period or "").strip()[:MAX_PERIOD_LENGTH]
  if not first or not last or not period:
    return {"ok": False, "message": "Enter your first name, last name, and class period."}
  row = app_tables.student_data.add_row(
    email=email,
    first_name=first,
    last_name=last,
    class_period=period,
    active=True,
    start_cash=STARTING_CASH,
    cash_balance=STARTING_CASH,
    holdings_value=0,
    total_value=STARTING_CASH,
    gain_loss=0,
    simcode=code,
    **{"return": 0},
  )
  return {"ok": True, "simcode": code, "existing": False}


@anvil.server.callable
def my_role(simcode):
  """Caller role ('teacher' / 'student' / None) for one simulation."""
  user = anvil.users.get_user()
  if user is None:
    return {"logged_in": False, "role": None}
  email = str(user["email"] or "").strip().lower()
  return {"logged_in": True, "role": _role_for(email, _clean_code(simcode))}
