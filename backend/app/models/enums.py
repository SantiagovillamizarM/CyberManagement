"""Estados y tipos usados en todo el sistema."""

from enum import StrEnum


class AdminRole(StrEnum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    AUDITOR = "auditor"  # solo lectura


class EmployeeStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ViaticoStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class MovementType(StrEnum):
    INITIAL = "initial"  # dinero con el que arrancó el viático
    ADDITIONAL = "additional"  # dinero adicional recibido
    REFUND = "refund"  # reintegro del empleado a la empresa
    ADJUSTMENT = "adjustment"  # corrección del administrador (puede ser negativa)


class MovementSource(StrEnum):
    EMPLOYEE = "employee"  # declarado por el empleado en Telegram
    ADMIN = "admin"  # registrado o confirmado por el administrador


class DocumentKind(StrEnum):
    INVOICE_PHOTO = "invoice_photo"
    RUT = "rut"
    OTHER = "other"


class ProcessingStatus(StrEnum):
    """Estado del procesamiento de una imagen por la IA."""

    RECEIVED = "received"
    ANALYZING = "analyzing"
    EXTRACTED = "extracted"  # escenario A
    NEEDS_MANUAL_INPUT = "needs_manual_input"  # escenario B
    NOT_INVOICE = "not_invoice"  # escenario C
    FAILED = "failed"  # error técnico, se puede reintentar


class ExtractionOutcome(StrEnum):
    EXTRACTED = "extracted"
    UNREADABLE = "unreadable"
    NOT_INVOICE = "not_invoice"
    ERROR = "error"


class ExpenseStatus(StrEnum):
    """Ciclo de vida de un gasto (estado de aprobación)."""

    DRAFT = "draft"  # el empleado lo está registrando
    PENDING_COMPLETION = "pending_completion"  # faltan datos (escenario B)
    SUBMITTED = "submitted"  # enviado para revisión
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CLOSED = "closed"


class PaymentSource(StrEnum):
    VIATICO = "viatico"  # pagado con dinero entregado por la empresa
    OWN_MONEY = "own_money"  # pagado por el empleado: reembolsable
    CORPORATE_CARD = "corporate_card"


class DataSource(StrEnum):
    AI = "ai"
    MANUAL = "manual"
    MIXED = "mixed"


class LocationSource(StrEnum):
    TELEGRAM = "telegram"  # compartida voluntariamente
    EXIF = "exif"  # metadatos GPS de la imagen
    MANUAL = "manual"
    NONE = "none"  # "No disponible"


class ActorType(StrEnum):
    ADMIN = "admin"
    EMPLOYEE = "employee"
    SYSTEM = "system"
    AI = "ai"


class NotificationStatus(StrEnum):
    PENDING = "pending"  # esperando su turno (o un reintento)
    SENDING = "sending"  # n8n lo tomó y lo está enviando
    SENT = "sent"
    FAILED = "failed"  # se agotaron los intentos o el empleado bloqueó el bot
