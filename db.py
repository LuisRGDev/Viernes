import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

def get_conn():
    """Crea y retorna una conexión a PostgreSQL en Supabase."""
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """Inicializa las tablas en Supabase si no existen."""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            message TEXT NOT NULL,
            remind_at TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        ALTER TABLE reminders ADD COLUMN IF NOT EXISTS recurrence_minutes INTEGER DEFAULT 0;
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS briefing_config (
            user_id BIGINT PRIMARY KEY,
            hour INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            timezone_offset INTEGER DEFAULT -6
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS google_tokens (
            user_id BIGINT PRIMARY KEY,
            token_json TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def add_task(user_id, description):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO tasks (user_id, description) VALUES (%s, %s) RETURNING id", (user_id, description))
    task_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return task_id

def list_tasks(user_id, status='pending'):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, description FROM tasks WHERE user_id = %s AND status = %s", (user_id, status))
    tasks = c.fetchall()
    conn.close()
    return tasks

def complete_task(task_id, user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE tasks SET status = 'completed' WHERE id = %s AND user_id = %s", (task_id, user_id))
    changes = c.rowcount
    conn.commit()
    conn.close()
    return changes > 0

def add_reminder(user_id, message, remind_at, recurrence_minutes=0):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO reminders (user_id, message, remind_at, recurrence_minutes) VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, message, remind_at, recurrence_minutes)
    )
    reminder_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return reminder_id

def get_pending_reminders(current_time):
    """Obtiene recordatorios cuya hora ya llegó o pasó."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, user_id, message, recurrence_minutes FROM reminders WHERE status = 'pending' AND remind_at <= %s",
        (current_time,)
    )
    reminders = c.fetchall()
    conn.close()
    return reminders

def mark_reminder_sent(reminder_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reminders SET status = 'sent' WHERE id = %s", (reminder_id,))
    conn.commit()
    conn.close()

def update_reminder_next_run(reminder_id, next_remind_at):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reminders SET remind_at = %s WHERE id = %s", (next_remind_at, reminder_id))
    conn.commit()
    conn.close()

def list_reminders(user_id):
    """Devuelve todos los recordatorios pendientes de un usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, message, remind_at, recurrence_minutes FROM reminders WHERE user_id = %s AND status = 'pending' ORDER BY remind_at ASC",
        (user_id,)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def cancel_reminder(reminder_id, user_id):
    """Cancela (elimina lógicamente) un recordatorio. Devuelve True si existía."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE reminders SET status = 'cancelled' WHERE id = %s AND user_id = %s AND status = 'pending'",
        (reminder_id, user_id)
    )
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def update_reminder_recurrence(reminder_id, user_id, new_recurrence_minutes):
    """Modifica la frecuencia de repetición de un recordatorio pendiente. Devuelve True si existía."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE reminders SET recurrence_minutes = %s WHERE id = %s AND user_id = %s AND status = 'pending'",
        (new_recurrence_minutes, reminder_id, user_id)
    )
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def set_briefing(user_id, hour, minute, timezone_offset=-6):
    """Guarda o actualiza la configuración del briefing para un usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO briefing_config (user_id, hour, minute, timezone_offset)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE
           SET hour = EXCLUDED.hour, minute = EXCLUDED.minute, timezone_offset = EXCLUDED.timezone_offset""",
        (user_id, hour, minute, timezone_offset)
    )
    conn.commit()
    conn.close()

def get_all_briefings():
    """Obtiene todos los usuarios con briefing configurado."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, hour, minute, timezone_offset FROM briefing_config")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_briefing(user_id):
    """Elimina la configuración de briefing de un usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM briefing_config WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()

def save_google_token(user_id, token_json):
    """Guarda o actualiza el token de Google para un usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO google_tokens (user_id, token_json)
           VALUES (%s, %s)
           ON CONFLICT (user_id) DO UPDATE
           SET token_json = EXCLUDED.token_json""",
        (user_id, token_json)
    )
    conn.commit()
    conn.close()

def get_google_token(user_id):
    """Obtiene el token de Google guardado de un usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT token_json FROM google_tokens WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    return None

# Inicializar la base de datos al importar este módulo
init_db()
