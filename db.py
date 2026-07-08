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
    # Proyecto IRON MAN — Sesiones de entrenamiento
    c.execute('''
        CREATE TABLE IF NOT EXISTS workout_sessions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL DEFAULT 'Entrenamiento',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            notes TEXT DEFAULT ''
        )
    ''')
    # Sets individuales por ejercicio
    c.execute('''
        CREATE TABLE IF NOT EXISTS workout_sets (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            exercise TEXT NOT NULL,
            set_number INTEGER NOT NULL DEFAULT 1,
            reps INTEGER NOT NULL,
            weight_kg REAL NOT NULL,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT DEFAULT ''
        )
    ''')
    # Alertas por condición
    c.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            description TEXT NOT NULL,
            condition_prompt TEXT NOT NULL,
            check_interval_min INTEGER NOT NULL DEFAULT 5,
            last_checked TIMESTAMP,
            triggered BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Notas rápidas
    c.execute('''
        CREATE TABLE IF NOT EXISTS notes (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Medicamentos
    c.execute('''
        CREATE TABLE IF NOT EXISTS medications (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            dose TEXT NOT NULL DEFAULT '1 dosis',
            frequency_hours INTEGER NOT NULL DEFAULT 24,
            reminder_time TEXT NOT NULL DEFAULT '08:00',
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS medication_logs (
            id SERIAL PRIMARY KEY,
            med_id INTEGER NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            skipped BOOLEAN DEFAULT FALSE
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

# ─── PROYECTO IRON MAN — GYM TRACKER ──────────────────────────────────────────────────

def start_workout_session(user_id, name):
    """Inicia una nueva sesión de entrenamiento. Devuelve el session_id."""
    conn = get_conn()
    c = conn.cursor()
    # Cerrar cualquier sesión abierta sin cerrar
    c.execute(
        "UPDATE workout_sessions SET ended_at = NOW(), notes = 'Cerrada automáticamente' "
        "WHERE user_id = %s AND ended_at IS NULL",
        (user_id,)
    )
    c.execute(
        "INSERT INTO workout_sessions (user_id, name) VALUES (%s, %s) RETURNING id",
        (user_id, name)
    )
    session_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return session_id

def get_active_session(user_id):
    """Devuelve la sesión activa (sin ended_at) o None."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, started_at FROM workout_sessions WHERE user_id = %s AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    return row  # (id, name, started_at) o None

def end_workout_session(session_id, notes=''):
    """Cierra una sesión de entrenamiento."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE workout_sessions SET ended_at = NOW(), notes = %s WHERE id = %s",
        (notes, session_id)
    )
    conn.commit()
    conn.close()

def log_set(session_id, user_id, exercise, set_number, reps, weight_kg, notes=''):
    """Registra una serie individual de un ejercicio."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO workout_sets (session_id, user_id, exercise, set_number, reps, weight_kg, notes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (session_id, user_id, exercise.strip(), set_number, reps, weight_kg, notes)
    )
    set_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return set_id

def get_session_sets(session_id):
    """Devuelve todos los sets de una sesión agrupados por ejercicio."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT exercise, set_number, reps, weight_kg, notes FROM workout_sets "
        "WHERE session_id = %s ORDER BY logged_at",
        (session_id,)
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(exercise, set_number, reps, weight_kg, notes), ...]

def get_next_set_number(session_id, exercise):
    """Devuelve el siguiente número de serie para un ejercicio en una sesión."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM workout_sets WHERE session_id = %s AND LOWER(exercise) = LOWER(%s)",
        (session_id, exercise)
    )
    count = c.fetchone()[0]
    conn.close()
    return count + 1

def get_exercise_pr(user_id, exercise):
    """Devuelve el peso máximo (PR) registrado para un ejercicio específico."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT MAX(weight_kg), reps, logged_at FROM workout_sets "
        "WHERE user_id = %s AND LOWER(exercise) = LOWER(%s) AND weight_kg = ("
        "  SELECT MAX(weight_kg) FROM workout_sets WHERE user_id = %s AND LOWER(exercise) = LOWER(%s)"
        ") ORDER BY logged_at DESC LIMIT 1",
        (user_id, exercise, user_id, exercise)
    )
    row = c.fetchone()
    conn.close()
    return row  # (max_weight, reps, logged_at) o None

def get_personal_records(user_id):
    """Devuelve el PR (máximo peso) por ejercicio para todos los ejercicios del usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT ws.exercise, ws.weight_kg, ws.reps, ws.logged_at
        FROM workout_sets ws
        INNER JOIN (
            SELECT LOWER(exercise) AS ex_lower, MAX(weight_kg) AS max_weight
            FROM workout_sets
            WHERE user_id = %s
            GROUP BY LOWER(exercise)
        ) pr ON LOWER(ws.exercise) = pr.ex_lower AND ws.weight_kg = pr.max_weight
        WHERE ws.user_id = %s
        ORDER BY ws.logged_at DESC
        """,
        (user_id, user_id)
    )
    rows = c.fetchall()
    conn.close()
    # Deduplicar por ejercicio (quedarse con el primero de cada uno)
    seen = set()
    result = []
    for exercise, weight, reps, logged_at in rows:
        key = exercise.lower()
        if key not in seen:
            seen.add(key)
            result.append((exercise, weight, reps, logged_at))
    return result  # [(exercise, weight_kg, reps, logged_at), ...]

def get_exercise_history(user_id, exercise, days=30):
    """Historial de un ejercicio: fecha, serie, reps, peso en los últimos N días."""
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT ws.logged_at::DATE, ws.set_number, ws.reps, ws.weight_kg
        FROM workout_sets ws
        WHERE ws.user_id = %s AND LOWER(ws.exercise) = LOWER(%s) AND ws.logged_at >= %s
        ORDER BY ws.logged_at
        """,
        (user_id, exercise, since)
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(date, set_number, reps, weight_kg), ...]

def get_last_session(user_id):
    """Devuelve la última sesión cerrada con sus sets."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, started_at, ended_at, notes FROM workout_sessions "
        "WHERE user_id = %s AND ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1",
        (user_id,)
    )
    session = c.fetchone()
    if not session:
        conn.close()
        return None, []
    session_id = session[0]
    c.execute(
        "SELECT exercise, set_number, reps, weight_kg FROM workout_sets WHERE session_id = %s ORDER BY logged_at",
        (session_id,)
    )
    sets = c.fetchall()
    conn.close()
    return session, sets  # (id, name, started_at, ended_at, notes), [(exercise, set_num, reps, weight), ...]

def get_weekly_workout_summary(user_id, since_date):
    """Cuenta sesiones y series completadas en la semana."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM workout_sessions WHERE user_id = %s AND ended_at IS NOT NULL AND started_at >= %s",
        (user_id, since_date)
    )
    session_count = c.fetchone()[0]
    c.execute(
        "SELECT COUNT(*), COALESCE(SUM(reps), 0) FROM workout_sets WHERE user_id = %s AND logged_at >= %s",
        (user_id, since_date)
    )
    sets_count, total_reps = c.fetchone()
    conn.close()
    return session_count, sets_count, total_reps  # sesiones, series, reps totales

# Inicializar la base de datos al importar este módulo
init_db()


# ─── ALERTAS POR CONDICIÓN ─────────────────────────────────────────────────────────────────

def add_alert(user_id, description, condition_prompt, check_interval_min=5):
    """Crea una nueva alerta de condición."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO alerts (user_id, description, condition_prompt, check_interval_min) VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, description, condition_prompt, check_interval_min)
    )
    alert_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return alert_id

def list_alerts(user_id):
    """Lista las alertas activas (no disparadas) del usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, description, condition_prompt, check_interval_min, last_checked FROM alerts WHERE user_id = %s AND triggered = FALSE ORDER BY created_at",
        (user_id,)
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(id, description, condition_prompt, interval, last_checked), ...]

def get_pending_alerts():
    """Devuelve alertas que deben ser revisadas ahora (no disparadas y cuyo intervalo ya pasó)."""
    from datetime import datetime, timedelta
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, user_id, description, condition_prompt, check_interval_min
        FROM alerts
        WHERE triggered = FALSE
          AND (last_checked IS NULL OR last_checked + (check_interval_min * INTERVAL '1 minute') <= NOW())
        """
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(id, user_id, description, condition_prompt, interval), ...]

def update_alert_last_checked(alert_id):
    """Actualiza el timestamp de última verificación."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE alerts SET last_checked = NOW() WHERE id = %s", (alert_id,))
    conn.commit()
    conn.close()

def mark_alert_triggered(alert_id):
    """Marca una alerta como disparada (ya notificada)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE alerts SET triggered = TRUE, last_checked = NOW() WHERE id = %s", (alert_id,))
    conn.commit()
    conn.close()

def delete_alert(alert_id, user_id):
    """Elimina una alerta por ID."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM alerts WHERE id = %s AND user_id = %s", (alert_id, user_id))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# ─── NOTAS RÁPIDAS ────────────────────────────────────────────────────────────────────

def add_note(user_id, content, tags=''):
    """Guarda una nota rápida de texto libre."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO notes (user_id, content, tags) VALUES (%s, %s, %s) RETURNING id",
        (user_id, content.strip(), tags.strip())
    )
    note_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return note_id

def search_notes(user_id, query):
    """Busca notas por contenido (búsqueda case-insensitive)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, content, tags, created_at FROM notes WHERE user_id = %s AND content ILIKE %s ORDER BY created_at DESC",
        (user_id, f'%{query}%')
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(id, content, tags, created_at), ...]

def list_recent_notes_db(user_id, limit=10):
    """Devuelve las últimas N notas del usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, content, tags, created_at FROM notes WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def delete_note(note_id, user_id):
    """Elimina una nota por ID."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM notes WHERE id = %s AND user_id = %s", (note_id, user_id))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# ─── MEDICAMENTOS ────────────────────────────────────────────────────────────────────

def add_medication(user_id, name, dose, frequency_hours, reminder_time):
    """Registra un nuevo medicamento con su horario de toma."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO medications (user_id, name, dose, frequency_hours, reminder_time) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (user_id, name.strip(), dose.strip(), frequency_hours, reminder_time)
    )
    med_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return med_id

def list_medications(user_id):
    """Lista los medicamentos activos del usuario."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, dose, frequency_hours, reminder_time FROM medications WHERE user_id = %s AND active = TRUE ORDER BY created_at",
        (user_id,)
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(id, name, dose, freq_hours, reminder_time), ...]

def get_due_medications(now_time_str):
    """Devuelve medicamentos activos cuya hora de recordatorio coincide con la hora actual (HH:MM)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, user_id, name, dose FROM medications WHERE active = TRUE AND reminder_time = %s",
        (now_time_str,)
    )
    rows = c.fetchall()
    conn.close()
    return rows  # [(id, user_id, name, dose), ...]

def log_medication_taken(med_id, user_id):
    """Registra que el usuario tomó el medicamento."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO medication_logs (med_id, user_id, skipped) VALUES (%s, %s, FALSE)",
        (med_id, user_id)
    )
    conn.commit()
    conn.close()

def log_medication_skipped(med_id, user_id):
    """Registra que el usuario omitió el medicamento."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO medication_logs (med_id, user_id, skipped) VALUES (%s, %s, TRUE)",
        (med_id, user_id)
    )
    conn.commit()
    conn.close()

def get_medication_adherence(med_id, user_id, days=30):
    """Calcula el porcentaje de días tomado correctamente en los últimos N días."""
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM medication_logs WHERE med_id = %s AND user_id = %s AND skipped = FALSE AND taken_at >= %s",
        (med_id, user_id, since)
    )
    taken = c.fetchone()[0]
    c.execute(
        "SELECT COUNT(*) FROM medication_logs WHERE med_id = %s AND user_id = %s AND taken_at >= %s",
        (med_id, user_id, since)
    )
    total = c.fetchone()[0]
    conn.close()
    pct = round((taken / days) * 100) if days > 0 else 0
    return taken, total, pct  # dias_tomado, total_registros, porcentaje

def delete_medication(med_id, user_id):
    """Desactiva un medicamento (soft delete)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE medications SET active = FALSE WHERE id = %s AND user_id = %s", (med_id, user_id))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def get_medication_weekly_stats(user_id, since_date):
    """Total de tomas registradas esta semana."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM medication_logs WHERE user_id = %s AND skipped = FALSE AND taken_at >= %s",
        (user_id, since_date)
    )
    taken = c.fetchone()[0]
    conn.close()
    return taken
