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
    # Memoria persistente del usuario
    c.execute('''
        CREATE TABLE IF NOT EXISTS memory (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key)
        )
    ''')
    # Hábitos diarios
    c.execute('''
        CREATE TABLE IF NOT EXISTS habits (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS habit_logs (
            id SERIAL PRIMARY KEY,
            habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            logged_date DATE NOT NULL,
            UNIQUE(habit_id, logged_date)
        )
    ''')
    # Watchlist (libros, películas, series)
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            title TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'movie',
            status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT DEFAULT '',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    # Sesiones Pomodoro
    c.execute('''
        CREATE TABLE IF NOT EXISTS pomodoro_sessions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 25,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def update_reminders_status_pending(reminder_id):
    """Reactiva un recordatorio a 'pending' (usado al snooze para que vuelva a dispararse)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reminders SET status = 'pending' WHERE id = %s", (reminder_id,))
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

# ─── MEMORIA PERSISTENTE ───────────────────────────────────────────────────────

def save_memory(user_id, key, value):
    """Guarda o actualiza un dato en la memoria del usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO memory (user_id, key, value, updated_at)
           VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
           ON CONFLICT (user_id, key) DO UPDATE
           SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP""",
        (user_id, key, value)
    )
    conn.commit()
    conn.close()

def get_all_memory(user_id):
    """Devuelve todos los datos memorizados del usuario como lista de (key, value)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT key, value FROM memory WHERE user_id = %s ORDER BY key", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_memory(user_id, key):
    """Elimina un dato de la memoria del usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM memory WHERE user_id = %s AND key = %s", (user_id, key))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

# ─── HÁBITOS ──────────────────────────────────────────────────────────────────

def add_habit(user_id, name):
    """Crea un nuevo hábito. Devuelve su ID."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO habits (user_id, name) VALUES (%s, %s) RETURNING id", (user_id, name))
    habit_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return habit_id

def delete_habit(habit_id, user_id):
    """Elimina un hábito y sus registros. Devuelve True si existía."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM habits WHERE id = %s AND user_id = %s", (habit_id, user_id))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def log_habit(habit_id, user_id):
    """Marca un hábito como completado hoy. Devuelve True si fue nuevo (no duplicado)."""
    from datetime import date
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO habit_logs (habit_id, user_id, logged_date) VALUES (%s, %s, %s)",
            (habit_id, user_id, date.today().isoformat())
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.rollback()
        conn.close()
        return False  # Ya estaba registrado hoy

def get_habit_streak(habit_id):
    """Calcula los días consecutivos completados hasta hoy (racha)."""
    from datetime import date, timedelta
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT logged_date FROM habit_logs WHERE habit_id = %s ORDER BY logged_date DESC",
        (habit_id,)
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return 0
    streak = 0
    check_date = date.today()
    for (logged_date,) in rows:
        if logged_date == check_date:
            streak += 1
            check_date -= timedelta(days=1)
        elif logged_date == check_date - timedelta(days=1):
            # Permitir que la racha incluya ayer si hoy aún no se ha registrado
            streak += 1
            check_date = logged_date - timedelta(days=1)
        else:
            break
    return streak

def list_habits(user_id):
    """Lista los hábitos del usuario con su racha y si ya se completaron hoy."""
    from datetime import date
    today = date.today().isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT h.id, h.name,
               EXISTS(
                   SELECT 1 FROM habit_logs hl
                   WHERE hl.habit_id = h.id AND hl.logged_date = %s
               ) AS done_today
        FROM habits h
        WHERE h.user_id = %s
        ORDER BY h.created_at
        """,
        (today, user_id)
    )
    rows = c.fetchall()
    conn.close()
    result = []
    for habit_id, name, done_today in rows:
        streak = get_habit_streak(habit_id)
        result.append((habit_id, name, done_today, streak))
    return result

# ─── RESUMEN SEMANAL ──────────────────────────────────────────────────────────

def get_completed_tasks_since(user_id, since_date):
    """Devuelve tareas completadas desde una fecha dada (formato 'YYYY-MM-DD')."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT description FROM tasks WHERE user_id = %s AND status = 'completed' AND created_at >= %s",
        (user_id, since_date)
    )
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_habit_weekly_summary(user_id):
    """Devuelve nombre del hábito y cuántos días lo completó esta semana (últimos 7 días)."""
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=6)).isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT h.name, COUNT(hl.id) AS days_completed
        FROM habits h
        LEFT JOIN habit_logs hl ON hl.habit_id = h.id AND hl.logged_date >= %s
        WHERE h.user_id = %s
        GROUP BY h.id, h.name
        ORDER BY h.created_at
        """,
        (since, user_id)
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(name, days_completed), ...]

# ─── WATCHLIST ──────────────────────────────────────────────────────────────────

def add_to_watchlist(user_id, title, media_type):
    """Agrega un ítem a la watchlist. media_type: 'book', 'movie' o 'series'."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO watchlist (user_id, title, type) VALUES (%s, %s, %s) RETURNING id",
        (user_id, title, media_type)
    )
    item_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return item_id

def list_watchlist(user_id, media_type=None, status=None):
    """Lista ítems de la watchlist con filtros opcionales."""
    conn = get_conn()
    c = conn.cursor()
    query = "SELECT id, title, type, status, notes FROM watchlist WHERE user_id = %s"
    params = [user_id]
    if media_type:
        query += " AND type = %s"
        params.append(media_type)
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY added_at DESC"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return rows  # [(id, title, type, status, notes), ...]

def update_watchlist_item(item_id, user_id, status, notes=''):
    """Actualiza el estado de un ítem de la watchlist."""
    from datetime import datetime
    conn = get_conn()
    c = conn.cursor()
    completed_at = datetime.now() if status == 'done' else None
    c.execute(
        "UPDATE watchlist SET status = %s, notes = %s, completed_at = %s WHERE id = %s AND user_id = %s",
        (status, notes, completed_at, item_id, user_id)
    )
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def delete_from_watchlist(item_id, user_id):
    """Elimina un ítem de la watchlist."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM watchlist WHERE id = %s AND user_id = %s", (item_id, user_id))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

# ─── POMODORO ───────────────────────────────────────────────────────────────────

def add_pomodoro_session(user_id, duration_minutes):
    """Registra una sesión Pomodoro completada."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO pomodoro_sessions (user_id, duration_minutes) VALUES (%s, %s) RETURNING id",
        (user_id, duration_minutes)
    )
    session_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return session_id

def get_pomodoro_count_since(user_id, since_date):
    """Total de sesiones Pomodoro completadas desde una fecha."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*), COALESCE(SUM(duration_minutes), 0) FROM pomodoro_sessions WHERE user_id = %s AND completed_at >= %s",
        (user_id, since_date)
    )
    row = c.fetchone()
    conn.close()
    return row  # (count, total_minutes)

# Inicializar la base de datos al importar este módulo
init_db()
