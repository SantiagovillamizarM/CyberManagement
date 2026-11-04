"""Todos los textos del bot en un solo lugar (se pueden editar sin tocar la lógica).

Los mensajes usan formato HTML de Telegram (<b>, <i>). Cualquier dato que venga
de un usuario debe pasar por html.escape antes de insertarse.
"""

WELCOME = (
    "¡Bienvenido a <b>CyberManagement</b>! 👋\n\n"
    "Para comenzar, necesito verificar tu identidad."
)

# Resumen de la autorización. La política completa debe publicarla la empresa
# (pendiente: enlace oficial). Cambiar este texto exige subir DATA_POLICY_VERSION.
CONSENT_REQUEST = (
    "🔒 <b>Autorización de tratamiento de datos personales</b>\n\n"
    "Conforme a la Ley 1581 de 2012, para registrar tus gastos la empresa tratará:\n"
    "• Tu nombre, identificación interna y usuario de Telegram.\n"
    "• Las fotos de facturas y los datos que contengan.\n"
    "• La ubicación, <i>solo si decides compartirla</i>.\n\n"
    "Finalidad: control y legalización de gastos y viáticos. Las imágenes pueden ser "
    "procesadas por un servicio de inteligencia artificial de Google, fuera de Colombia.\n\n"
    "Puedes conocer, actualizar, rectificar y solicitar la supresión de tus datos "
    "contactando al administrador.\n\n"
    "¿Autorizas el tratamiento de tus datos?"
)
CONSENT_ACCEPT = "✅ Acepto"
CONSENT_DECLINE = "❌ No acepto"
CONSENT_DECLINED = (
    "Entendido. Sin tu autorización no podemos registrarte.\n"
    "Si cambias de opinión, escribe /start."
)
CONSENT_USE_BUTTONS = "Por favor responde usando los botones de arriba 👆"

ASK_CODE = (
    "Por favor, introduce el <b>código de registro</b> que te proporcionó el administrador.\n"
    "Ejemplo: <code>JHON-7KQ4-M9XT</code>"
)
CODE_NEEDS_TEXT = "Primero necesito tu código de registro. Escríbelo como texto, por favor."
REGISTER_SUCCESS = (
    "🎉 <b>¡Registro exitoso!</b>\n\n"
    "Bienvenido, {first_name}.\n"
    "Tu cuenta ha sido vinculada correctamente a CyberManagement."
)
CODE_INVALID = (
    "❌ El código no es válido o ya venció.\n"
    "Revísalo e inténtalo de nuevo, o pide uno nuevo al administrador."
)
CODE_TOO_MANY = "⏳ Demasiados intentos fallidos. Espera 15 minutos e inténtalo de nuevo."
EMPLOYEE_INACTIVE = "🚫 Tu cuenta está deshabilitada. Contacta al administrador."
ALREADY_LINKED = "Tu cuenta ya está vinculada, {first_name}."

MENU = "Hola, {first_name}. ¿Qué deseas hacer?"
MENU_EXPENSE = "📸 Registrar gasto"
MENU_BALANCE = "💰 Mi saldo"
MENU_EXPENSES = "🧾 Mis gastos"
MENU_HELP = "❓ Ayuda"
EXPENSE_COMING_SOON = "🚧 El registro de gastos estará disponible muy pronto."

NO_OPEN_VIATICOS = "No tienes viáticos abiertos en este momento."
BALANCE_HEADER = "💰 <b>Tus viáticos abiertos</b>"
BALANCE_LINE = (
    "\n\n<b>{purpose}</b>\n"
    "Entregado: {delivered}\n"
    "Gastado: {spent}\n"
    "Disponible: <b>{remaining}</b>"
)
NO_EXPENSES = "Todavía no has registrado gastos."
EXPENSES_HEADER = "🧾 <b>Tus últimos gastos</b>"
EXPENSE_LINE = "\n• {date} — {description}: {total} <i>({status})</i>"

EXPENSE_STATUS_LABELS = {
    "draft": "borrador",
    "pending_completion": "pendiente de completar",
    "submitted": "enviado",
    "in_review": "en revisión",
    "approved": "aprobado",
    "rejected": "rechazado",
    "closed": "cerrado",
}

HELP_UNLINKED = (
    "<b>Ayuda</b>\n\n"
    "Para usar CyberManagement necesitas el código de registro que te entrega el "
    "administrador.\n\n"
    "/start — comenzar el registro\n"
    "/cancelar — cancelar lo que estás haciendo"
)
HELP_LINKED = (
    "<b>Ayuda</b>\n\n"
    "/menu — ver el menú principal\n"
    "/saldo — consultar tus viáticos\n"
    "/gastos — ver tus últimos gastos\n"
    "/cancelar — cancelar lo que estás haciendo\n\n"
    "Si tienes problemas con tu cuenta, contacta al administrador."
)
CANCELLED = "Operación cancelada."
CANCELLED_UNLINKED = "Operación cancelada. Escribe /start cuando quieras comenzar."
