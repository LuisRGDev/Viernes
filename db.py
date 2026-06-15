import sqlite3
import datetime

DB_NAME = 'assistant.db'

def init_db():
    """Inicializa las tablas de la base de datos."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            remind_at TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS briefing_config (
            user_id INTEGER PRIMARY KEY,
            hour INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            timezone_offset INTEGER DEFAULT -6
        )
    ''')
    conn.commit()
    conn.close()

def add_task(user_id, description):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO tasks (user_id, description) VALUES (?, ?)", (user_id, description))
    conn.commit()
    task_id = c.lastrowid
    conn.close()
    return task_id

def list_tasks(user_id, status='pending'):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, description FROM tasks WHERE user_id = ? AND status = ?", (user_id, status))
    tasks = c.fetchall()
    conn.close()
    return tasks

def complete_task(task_id, user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET status = 'completed' WHERE id = ? AND user_id = ?", (task_id, user_id))
    changes = conn.total_changes
    conn.commit()
    conn.close()
    return changes > 0

def add_reminder(user_id, message, remind_at):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO reminders (user_id, message, remind_at) VALUES (?, ?, ?)", (user_id, message, remind_at))
    conn.commit()
    reminder_id = c.lastrowid
    conn.close()
    return reminder_id

def get_pending_reminders(current_time):
    """Obtiene los recordatorios cuya hora de alerta ya llegó o pasó."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, user_id, message FROM reminders WHERE status = 'pending' AND remind_at <= ?", (current_time,))
    reminders = c.fetchall()
    conn.close()
    return reminders

def mark_reminder_sent(reminder_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def set_briefing(user_id, hour, minute, timezone_offset=-6):
    """Guarda o actualiza la configuración del briefing para un usuario."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO briefing_config (user_id, hour, minute, timezone_offset) VALUES (?, ?, ?, ?)",
        (user_id, hour, minute, timezone_offset)
    )
    conn.commit()
    conn.close()

def get_all_briefings():
    """Obtiene todos los usuarios con briefing configurado."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, hour, minute, timezone_offset FROM briefing_config")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_briefing(user_id):
    """Elimina la configuración de briefing de un usuario."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM briefing_config WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Inicializamos la base de datos al importar este módulo
init_db()
