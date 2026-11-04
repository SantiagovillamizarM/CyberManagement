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
    "Entendido. Sin tu autorización no puedo registrarte, así que aquí terminamos por ahora.\n"
    "Si cambias de opinión, escríbeme cualquier mensaje y te la vuelvo a mostrar."
)
# Antecede a la autorización cuando se repite porque la persona respondió otra cosa.
CONSENT_PENDING = "Para continuar necesito tu respuesta a esta autorización 👇"

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

# --- Registro de gastos (paso 4.2) ---

SKIP = "Omitir ⏭️"
EXP_WHICH_MONEY = "💰 <b>¿Con qué dinero pagaste este gasto?</b>"
EXP_VIATICO_OPTION = "💼 {purpose} · disponible {remaining}"
EXP_NEW_VIATICO_OPTION = "➕ Tenía otro dinero previo"
EXP_OWN_MONEY_OPTION = "👛 Con mi dinero (reembolsable)"
EXP_HAD_MONEY = "💰 ¿Contabas con dinero previo para realizar estos gastos (viáticos)?"
YES, NO = "Sí", "No"
EXP_ASK_INITIAL = "¿Cuánto dinero tenías disponible inicialmente?\nEjemplo: <code>500.000</code>"
EXP_ASK_PURPOSE = "¿Para qué es ese dinero? Ejemplo: <i>Viaje a Medellín</i>"
EXP_ASK_PHOTO = (
    "📸 Envía la <b>foto de la factura</b>.\n\n"
    "💡 Si la envías como <b>archivo</b> (📎 → Archivo) se conserva la ubicación GPS de la foto.\n"
    "Si no tienes foto, pulsa «Registrar sin foto»."
)
EXP_NO_PHOTO = "✍️ Registrar sin foto"
EXP_RECEIVING = "📥 Recibiendo el archivo…"
EXP_PHOTO_SAVED = "✅ Foto de la factura guardada."
EXP_PHOTO_SAVED_GPS = "✅ Foto de la factura guardada (incluye ubicación GPS)."
EXP_ALREADY_HAVE_PHOTO = "Ya tengo la foto de esta factura. Si quieres cambiarla, usa ✏️ Editar → Foto."
EXP_PHOTO_IN_OTHER_EXPENSE = (
    "⚠️ Esta foto ya está registrada en otro gasto, así que no la asocié.\n"
    "Envía otra foto o continúa sin foto."
)
EXP_ASK_RUT = "📄 ¿Tienes el <b>RUT</b> del proveedor? Envíalo como foto o PDF.\n<i>Es opcional.</i>"
EXP_RUT_SAVED = "✅ RUT guardado."
EXP_FILE_TOO_LARGE = "⚠️ El archivo pesa más de {mb} MB. Envía uno más liviano."
EXP_FILE_TYPE = "⚠️ Solo puedo recibir fotos (JPG, PNG, WEBP) o archivos PDF."
EXP_FILE_INVALID = "⚠️ No pude usar ese archivo: {reason}"
EXP_FILE_NO_FLOW = (
    "📥 Guardé tu archivo. Para registrar un gasto con él, toca «📸 Registrar gasto» "
    "y envíalo de nuevo."
)
EXP_ASK_DESCRIPTION ="📝 Describe el gasto.\nEjemplo: <i>Almuerzo con cliente</i>"
EXP_ASK_TOTAL = "💵 ¿Cuál es el <b>valor total</b> de la factura?\nEjemplo: <code>45.000</code>"
EXP_ASK_UNIT = (
    "🔢 ¿Cuál es el <b>valor unitario</b>?\n"
    "Útil si compraste varias unidades del mismo producto. Si no aplica, omítelo."
)
EXP_ASK_DATE = "📅 ¿Qué <b>fecha</b> tiene la factura?\nO escríbela así: <code>03/11/2026</code>"
TODAY, YESTERDAY = "Hoy", "Ayer"
EXP_ASK_NIT = (
    "🏪 ¿Cuál es el <b>NIT</b> del establecimiento?\n"
    "Ejemplo: <code>900.123.456-8</code>. Si la factura no tiene NIT, omítelo."
)
EXP_KNOWN_SUPPLIER = "Establecimiento: <b>{name}</b> ✅"
EXP_ASK_SUPPLIER_NAME = "¿Cómo se llama el establecimiento?"
EXP_ASK_INVOICE = "#️⃣ ¿Cuál es el <b>número de la factura</b>? Ejemplo: <code>FE-1234</code>"
EXP_ASK_CATEGORY = "🏷️ ¿Qué <b>tipo de gasto</b> es?"
EXP_ASK_LOCATION = (
    "📍 ¿Estás en el lugar de la compra? Compartir tu ubicación ayuda a verificar el gasto.\n"
    "<i>Es opcional.</i>"
)
EXP_SHARE_LOCATION = "📍 Compartir ubicación"
EXP_LOCATION_SAVED = "📍 Ubicación registrada."
EXP_LOCATION_SKIPPED = "Sin ubicación."

EXP_SUMMARY = (
    "🧾 <b>Resumen del gasto</b>\n\n"
    "<b>Descripción:</b> {description}\n"
    "<b>Valor total:</b> {total}\n"
    "<b>Valor unitario:</b> {unit_price}\n"
    "<b>Fecha de la factura:</b> {issue_date}\n"
    "<b>Establecimiento:</b> {supplier}\n"
    "<b>N.º de factura:</b> {invoice_number}\n"
    "<b>Tipo de gasto:</b> {category}\n"
    "<b>Pago:</b> {payment}\n"
    "<b>Ubicación:</b> {location}\n"
    "<b>Foto:</b> {photo}\n\n"
    "¿Los datos son correctos?"
)
EXP_PAY_VIATICO = "Viático «{purpose}»"
EXP_PAY_NEW_VIATICO = "Viático nuevo «{purpose}» ({amount} iniciales)"
EXP_PAY_OWN = "Dinero propio (reembolsable)"
NOT_AVAILABLE = "No disponible"
NO_NIT = "Sin NIT"
EXP_CONFIRM, EXP_EDIT, EXP_CANCEL = "✅ Confirmar", "✏️ Editar", "❌ Cancelar"
EXP_WHICH_FIELD = "✏️ ¿Qué dato quieres corregir?"
EXP_BACK_TO_SUMMARY = "↩️ Volver al resumen"
EXP_FIELD_LABELS = {
    "photo": "Foto",
    "description": "Descripción",
    "total": "Valor total",
    "unit_price": "Valor unitario",
    "issue_date": "Fecha",
    "nit": "NIT",
    "invoice_number": "N.º de factura",
    "category": "Tipo de gasto",
    "location": "Ubicación",
}

EXP_SAVED = "✅ <b>Gasto registrado</b> y enviado para revisión."
EXP_SAVED_BALANCE = "\nDisponible en «{purpose}»: <b>{remaining}</b>"
EXP_SAVED_REIMBURSABLE = "\nQuedó marcado como <b>reembolsable</b>."
EXP_DUPLICATE = (
    "⚠️ Ya existe un gasto registrado con esa factura ({reason}).\n"
    "Si es otra factura, corrige el dato; si no, cancela el registro."
)
EXP_COULD_NOT_SAVE = "⚠️ No pude guardar el gasto: {reason}"
EXP_CANCELLED = "Registro de gasto cancelado. No se guardó nada."
EXP_USE_LATEST = "Usa las opciones del último mensaje, por favor 👇"
EXP_BAD_AMOUNT = (
    "No entendí el valor. Escríbelo solo con números, por ejemplo <code>45000</code> o <code>45.000</code>."
)
EXP_UNIT_GT_TOTAL = "El valor unitario no puede ser mayor que el valor total."
EXP_BAD_TEXT = "Escribe un texto de entre {min} y {max} caracteres."

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

# --- Corrección de gastos (paso 4.4) ---

FIX_LIST_HEADER = "🧾 <b>Tus últimos gastos</b>\nToca uno para ver el detalle."
FIX_DETAIL = (
    "🧾 <b>Gasto del {date}</b>\n\n"
    "<b>Descripción:</b> {description}\n"
    "<b>Valor total:</b> {total}\n"
    "<b>Establecimiento:</b> {supplier}\n"
    "<b>N.º de factura:</b> {invoice_number}\n"
    "<b>Tipo de gasto:</b> {category}\n"
    "<b>Fotos:</b> {photos}\n"
    "<b>Estado:</b> {status}"
)
FIX_REJECTED = "\n\n❗ <b>Motivo del rechazo:</b> {reason}"
FIX_LOCKED = "\n\n🔒 Este gasto ya no se puede modificar."
FIX_CAN_RESUBMIT = "\n\nCorrige lo necesario y pulsa «📤 Reenviar»."
FIX_EDIT = "✏️ Corregir"
FIX_RESUBMIT = "📤 Reenviar"
FIX_BACK_TO_LIST = "↩️ Mis gastos"
FIX_WHICH_FIELD = "✏️ ¿Qué dato quieres corregir?"
FIX_ADD_PHOTO = "📸 Agregar foto"
FIX_PHOTO_VIA_MENU = "Para agregar una foto a este gasto usa ✏️ Corregir → 📸 Agregar foto."
FIX_ASK_PHOTO ="📸 Envía la nueva foto de la factura (como archivo conserva el GPS)."
FIX_SAVED = "✅ Corrección guardada."
FIX_PHOTO_ADDED = "✅ Foto agregada al gasto."
FIX_NO_CHANGES = "No hubo cambios."
FIX_RESUBMITTED = "📤 <b>Gasto reenviado</b> para revisión."
FIX_NOT_FOUND = "No encontré ese gasto."
FIX_LOCKED_ERROR = "🔒 {reason}"
FIX_CONFLICT = (
    "⚠️ El administrador modificó este gasto mientras lo corregías. "
    "Te muestro la versión actual; revisa y vuelve a intentarlo."
)

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
CANCELLED_UNLINKED = "Operación cancelada. Escríbeme cualquier mensaje cuando quieras comenzar."
