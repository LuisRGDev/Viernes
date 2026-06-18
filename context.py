import contextvars

# ContextVar para saber qué usuario está ejecutando la herramienta
current_user_id = contextvars.ContextVar('current_user_id')
