"""Importa todos los modelos para que Alembic los detecte."""

from app.models.admin import AdminSession, AdminUser
from app.models.audit import AuditLog
from app.models.company import Company
from app.models.document import AIExtraction, Document
from app.models.employee import DataConsent, Employee, RegistrationCode
from app.models.expense import Category, Expense, ExpenseItem, Supplier
from app.models.notification import Notification
from app.models.report import Report
from app.models.telegram import BotSession, RegistrationAttempt, TelegramEvent
from app.models.viatico import Viatico, ViaticoMovement

__all__ = [
    "AIExtraction",
    "AdminSession",
    "AdminUser",
    "AuditLog",
    "BotSession",
    "Category",
    "Company",
    "DataConsent",
    "Document",
    "Employee",
    "Expense",
    "ExpenseItem",
    "Notification",
    "RegistrationAttempt",
    "RegistrationCode",
    "Report",
    "Supplier",
    "TelegramEvent",
    "Viatico",
    "ViaticoMovement",
]
