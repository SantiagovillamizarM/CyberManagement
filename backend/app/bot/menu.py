"""Menú principal del empleado vinculado (compartido por el motor y los flujos)."""

import html

from app.bot import messages as msg
from app.bot.actions import Action, Keyboard, send_message
from app.models import Employee

MENU = "menu"  # estado de la sesión cuando el empleado está en el menú

CB_EXPENSE, CB_BALANCE, CB_EXPENSES, CB_HELP = "menu:expense", "menu:balance", "menu:expenses", "menu:help"

MENU_BUTTONS: Keyboard = [
    [(msg.MENU_EXPENSE, CB_EXPENSE)],
    [(msg.MENU_BALANCE, CB_BALANCE), (msg.MENU_EXPENSES, CB_EXPENSES)],
    [(msg.MENU_HELP, CB_HELP)],
]


def menu_action(chat_id: int, employee: Employee) -> Action:
    name = html.escape(employee.first_name or "")
    return send_message(chat_id, msg.MENU.format(first_name=name), MENU_BUTTONS)
