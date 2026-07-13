import os
import logging
import tempfile
import html
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import contextvars
from ddgs import DDGS
import edge_tts
from gtts import gTTS

from youtube_transcript_api import YouTubeTranscriptApi
from bs4 import BeautifulSoup
import requests
import urllib.parse

import db

def clean_for_tts(text: str) -> str:
    """Limpia el texto de formato Markdown antes de pasarlo al generador de voz."""
    # Eliminar negritas y cursivas
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
    # Eliminar encabezados
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Eliminar bullets
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
    # Eliminar links markdown
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Eliminar código inline
    text = re.sub(r'`[^`]+`', '', text)
    # Eliminar emojis y caracteres especiales problemáticos para TTS
    text = re.sub(r'[_~|>]', '', text)
    # Limpiar espacios extra
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.error("Error: Las variables de entorno no están configuradas.")
    exit(1)

# Configurar Gemini
genai.configure(api_key=GEMINI_API_KEY)

from context import current_user_id

# --- DEFINICIÓN DE HERRAMIENTAS PARA GEMINI ---
def add_new_task(description: str) -> str:
    """Guarda una nueva tarea pendiente para el usuario. Úsala siempre que el usuario te pida anotar o recordar algo para hacer después."""
    user_id = current_user_id.get()
    db.add_task(user_id, description)
    return f"He guardado la tarea en los protocolos: '{description}'."

def get_tasks() -> str:
    """Obtiene la lista de tareas pendientes del usuario."""
    user_id = current_user_id.get()
    tasks = db.list_tasks(user_id)
    if not tasks:
        return "Los registros indican que no tiene tareas pendientes, Señor."
    return "Tareas en el sistema:\n" + "\n".join([f"ID {t[0]}: {t[1]}" for t in tasks])

def mark_task_done(task_id: int) -> str:
    """Marca una tarea específica como completada usando su ID numérico."""
    user_id = current_user_id.get()
    success = db.complete_task(task_id, user_id)
    if success:
        return f"Tarea {task_id} purgada de la base de datos."
    return f"Error: No encuentro la tarea {task_id} en los registros."

def schedule_reminder(message: str, delay_minutes: float, recurrence_minutes: float = 0) -> str:
    """Programa un recordatorio o alarma que sonará en el futuro.
    
    Args:
        message: El texto del recordatorio que le llegará al usuario.
        delay_minutes: En cuántos minutos a partir de ahora se enviará la alarma.
        recurrence_minutes: Opcional. En cuántos minutos se repetirá periódicamente después de la primera vez. Usa 1440 para diario, 10080 para semanal (7 días), etc. Usa 0 si no se repite.
    """
    user_id = current_user_id.get()
    remind_at = datetime.now() + timedelta(minutes=delay_minutes)
    db.add_reminder(user_id, message, remind_at.strftime('%Y-%m-%d %H:%M:%S'), int(recurrence_minutes))
    
    if recurrence_minutes > 0:
        return f"Alarma recurrente configurada: '{message}'. Notificaré en {delay_minutes} minutos (a las {remind_at.strftime('%H:%M')}), y luego cada {recurrence_minutes} minutos, Jefe."
    else:
        return f"Alarma configurada: '{message}'. Le notificaré en {delay_minutes} minutos (a las {remind_at.strftime('%H:%M')}), Jefe."

def list_reminders() -> str:
    """Lista todos los recordatorios activos (pendientes) del usuario con su ID, mensaje, próxima ejecución y frecuencia. Úsala cuando el usuario pregunte qué recordatorios tiene, quiera ver sus alarmas o necesite el ID para cancelar o modificar uno."""
    user_id = current_user_id.get()
    reminders = db.list_reminders(user_id)
    if not reminders:
        return "No hay recordatorios activos en el sistema, Jefe."
    lines = []
    for r in reminders:
        r_id, message, remind_at, recurrence = r
        recurrence_str = f", se repite cada {recurrence} min" if recurrence and recurrence > 0 else ", una sola vez"
        lines.append(f"ID {r_id}: '{message}' — próxima vez a las {remind_at}{recurrence_str}")
    return "Recordatorios activos:\n" + "\n".join(lines)

def cancel_reminder(reminder_id: int) -> str:
    """Cancela y elimina un recordatorio activo usando su ID numérico. Úsala cuando el usuario pida cancelar, borrar o detener un recordatorio específico. Primero lista los recordatorios si el usuario no sabe el ID."""
    user_id = current_user_id.get()
    success = db.cancel_reminder(reminder_id, user_id)
    if success:
        return f"Recordatorio ID {reminder_id} cancelado y removido del sistema."
    return f"No encontré un recordatorio activo con ID {reminder_id} en los registros."

def modify_reminder_recurrence(reminder_id: int, new_recurrence_minutes: float) -> str:
    """Cambia la frecuencia de repetición de un recordatorio ya programado. Úsala cuando el usuario pida cambiar cada cuánto se repite una alarma. Usa 0 para convertirlo en un recordatorio de una sola vez. Primero lista los recordatorios si el usuario no sabe el ID."""
    user_id = current_user_id.get()
    success = db.update_reminder_recurrence(reminder_id, user_id, int(new_recurrence_minutes))
    if success:
        if new_recurrence_minutes > 0:
            return f"Listo, Jefe. El recordatorio ID {reminder_id} ahora se repite cada {new_recurrence_minutes} minutos."
        else:
            return f"Listo. El recordatorio ID {reminder_id} ahora solo se ejecutará una vez más y no se repetirá."
    return f"No encontré un recordatorio activo con ID {reminder_id} en los registros."

# ─── HERRAMIENTAS DE MEMORIA ──────────────────────────────────────────────────────────

def remember_fact(key: str, value: str) -> str:
    """Guarda un dato importante sobre el usuario en la memoria persistente para recordarlo en el futuro.
    Ejemplos: remember_fact('mascota', 'Rocky, perro labrador'), remember_fact('cumpleanos', '15 de marzo'),
    remember_fact('trabajo', 'ingeniero de software'), remember_fact('ciudad', 'Guadalajara').
    Úsala siempre que el usuario mencione datos personales relevantes que debes recordar a largo plazo."""
    user_id = current_user_id.get()
    db.save_memory(user_id, key.lower().strip(), value.strip())
    return f"Memorizado, Jefe: '{key}' = '{value}'. Lo recordaré en futuras conversaciones."

def recall_facts() -> str:
    """Recupera todos los datos que has memorizado sobre el usuario.
    Úsala cuando necesites contexto personal del usuario o cuando te pida qué recuerdas de él."""
    user_id = current_user_id.get()
    facts = db.get_all_memory(user_id)
    if not facts:
        return "Mi memoria personal sobre ti está vacía, Jefe. Cuéntame cosas importantes y las guardaré."
    lines = [f"- {k}: {v}" for k, v in facts]
    return "Lo que tengo memorizado sobre ti:\n" + "\n".join(lines)

def forget_fact(key: str) -> str:
    """Elimina un dato específico de la memoria del usuario. Úsala cuando el usuario pida que olvides algo o cuando un dato ya no sea válido."""
    user_id = current_user_id.get()
    success = db.delete_memory(user_id, key.lower().strip())
    if success:
        return f"Dato '{key}' eliminado de mi memoria, Jefe."
    return f"No tenía ningún dato llamado '{key}' en mis registros."

# ─── HERRAMIENTAS DE HÁBITOS ─────────────────────────────────────────────────────────

def add_habit(name: str) -> str:
    """Crea un nuevo hábito diario para rastrear. Úsala cuando el usuario quiera registrar un hábito como 'hacer ejercicio', 'beber agua', 'leer 30 minutos', etc."""
    user_id = current_user_id.get()
    habit_id = db.add_habit(user_id, name.strip())
    return f"Hábito '{name}' registrado con ID {habit_id}. Cada día que lo completes, dímelo y lo anoto."

def list_habits() -> str:
    """Muestra todos los hábitos del usuario con su racha actual y si ya lo completó hoy.
    Úsala cuando el usuario pregunte por sus hábitos, su progreso o su racha."""
    user_id = current_user_id.get()
    habits = db.list_habits(user_id)
    if not habits:
        return "No tienes hábitos registrados aún, Jefe. Díme cuáles quieres rastrear."
    lines = []
    for h_id, name, done_today, streak in habits:
        done_str = "✅ Hecho hoy" if done_today else "⏳ Pendiente hoy"
        streak_str = f"🔥 {streak} días de racha" if streak > 0 else "Sin racha aún"
        lines.append(f"ID {h_id}: {name} | {done_str} | {streak_str}")
    return "Tus hábitos:\n" + "\n".join(lines)

def complete_habit(habit_id: int) -> str:
    """Marca un hábito como completado hoy usando su ID. Úsala cuando el usuario diga que ya hizo uno de sus hábitos.
    Si no sabe el ID, usa list_habits primero."""
    user_id = current_user_id.get()
    success = db.log_habit(habit_id, user_id)
    if success:
        # Obtener la racha actualizada para feedback motivacional
        streak = db.get_habit_streak(habit_id)
        if streak >= 7:
            return f"🔥 ¡Hábito ID {habit_id} completado! Llevas {streak} días seguidos, Jefe. Imparable."
        elif streak >= 3:
            return f"✅ Hábito ID {habit_id} completado. Racha de {streak} días y contando."
        else:
            return f"✅ Hábito ID {habit_id} marcado como completado hoy."
    return f"El hábito ID {habit_id} ya estaba registrado como completado hoy."

def delete_habit(habit_id: int) -> str:
    """Elimina un hábito y todo su historial. Úsala cuando el usuario quiera dejar de rastrear un hábito."""
    user_id = current_user_id.get()
    success = db.delete_habit(habit_id, user_id)
    if success:
        return f"Hábito ID {habit_id} y su historial eliminados del sistema."
    return f"No encontré un hábito con ID {habit_id} en los registros."

# ─── HERRAMIENTAS DE WATCHLIST ────────────────────────────────────────────────────

TYPE_LABELS = {'book': '📚 Libro', 'movie': '🎬 Película', 'series': '📺 Serie'}
STATUS_LABELS = {'pending': '⏳ Pendiente', 'in_progress': '▶️ En progreso', 'done': '✅ Terminado'}

def add_to_watchlist(title: str, media_type: str) -> str:
    """Agrega un libro, película o serie a la lista de pendientes del usuario.
    media_type debe ser exactamente 'book', 'movie' o 'series'.
    Úsala cuando el usuario diga 'agrega X a mi lista', 'quiero ver X', 'anota que quiero leer X'."""
    user_id = current_user_id.get()
    if media_type not in ('book', 'movie', 'series'):
        return "Tipo inválido. Usa 'book', 'movie' o 'series'."
    item_id = db.add_to_watchlist(user_id, title.strip(), media_type)
    label = TYPE_LABELS.get(media_type, media_type)
    return f"{label} '{title}' agregado a tu watchlist con ID {item_id}."

def list_watchlist(media_type: str = '', status: str = '') -> str:
    """Muestra la watchlist del usuario. Filtra por tipo ('book','movie','series') y/o estado ('pending','in_progress','done').
    Deja vacío para ver todo. Úsala cuando el usuario pregunte qué tiene en su lista, qué quiere ver, o qué quiere leer."""
    user_id = current_user_id.get()
    items = db.list_watchlist(user_id, media_type or None, status or None)
    if not items:
        return "Tu watchlist está vacía, Jefe. Agrega libros, películas o series."
    lines = []
    for i_id, title, mtype, mstatus, notes in items:
        label = TYPE_LABELS.get(mtype, mtype)
        slabel = STATUS_LABELS.get(mstatus, mstatus)
        note_str = f" — {notes}" if notes else ""
        lines.append(f"ID {i_id}: {label} '{title}' | {slabel}{note_str}")
    return "Tu watchlist:\n" + "\n".join(lines)

def update_watchlist_item(item_id: int, status: str, notes: str = '') -> str:
    """Actualiza el estado de un ítem de la watchlist. status: 'pending', 'in_progress' o 'done'.
    Úsala cuando el usuario diga que ya vio/leyó algo, que está en progreso, o quiera agregar una nota.
    Si no sabe el ID, usa list_watchlist primero."""
    user_id = current_user_id.get()
    if status not in ('pending', 'in_progress', 'done'):
        return "Estado inválido. Usa 'pending', 'in_progress' o 'done'."
    success = db.update_watchlist_item(item_id, user_id, status, notes)
    if success:
        slabel = STATUS_LABELS.get(status, status)
        note_str = f" Nota guardada: '{notes}'." if notes else ""
        return f"Ítem ID {item_id} actualizado a {slabel}.{note_str}"
    return f"No encontré el ítem ID {item_id} en tu watchlist."

def remove_from_watchlist(item_id: int) -> str:
    """Elimina un ítem de la watchlist usando su ID. Úsala cuando el usuario ya no quiera rastrear algo."""
    user_id = current_user_id.get()
    success = db.delete_from_watchlist(item_id, user_id)
    if success:
        return f"Ítem ID {item_id} eliminado de la watchlist."
    return f"No encontré el ítem ID {item_id} en tu watchlist."

def search_web(query: str) -> str:
    """Busca en internet en tiempo real para obtener información actualizada. Úsalo SIEMPRE que te pregunten sobre noticias recientes, precios actuales de monedas, clima actual, fechas de eventos futuros o cualquier información que pueda cambiar con el tiempo. NUNCA inventes información reciente."""
    try:
        results = DDGS().text(query, max_results=8)
        if not results:
            return "No encontré datos en la red global."
        response = ""
        for r in results:
            response += f"Título: {r['title']}\nResumen: {r['body']}\n\n"
        return response
    except Exception as e:
        logger.error(f"Error en búsqueda web: {e}")
        return "Problema de conexión con la red global."

def generate_image_url(prompt: str) -> str:
    """Genera una imagen a partir de un texto. Úsala siempre que el usuario pida dibujar, crear o generar una imagen o foto.
    Esta función devuelve una URL de la imagen generada.
    IMPORTANTE: Debes responder al usuario enviando ESTRICTAMENTE la URL devuelta para que Telegram muestre la imagen."""
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true"
    return f"Aquí tienes la URL de la imagen generada, envíasela al usuario: {url}"

def get_youtube_transcript(video_url: str) -> str:
    """Obtiene los subtítulos o transcripción de un video de YouTube para poder resumirlo. Úsala cuando el usuario te pida resumir un video de YouTube."""
    try:
        if "v=" in video_url:
            video_id = video_url.split("v=")[1][:11]
        elif "youtu.be/" in video_url:
            video_id = video_url.split("youtu.be/")[1][:11]
        else:
            return "Error: No pude identificar el ID del video de YouTube."
            
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # Buscar español primero, luego inglés, o auto-generados
        try:
            transcript = transcript_list.find_transcript(['es', 'en'])
        except:
            transcript = transcript_list.find_generated_transcript(['es', 'en'])
            
        data = transcript.fetch()
        text = " ".join([item['text'] for item in data])
        return f"Transcripción del video:\n\n{text[:15000]}"
    except Exception as e:
        return f"No se pudo obtener la transcripción: {e}"

def scrape_website(url: str) -> str:
    """Lee el texto principal de una página web o artículo. Úsala cuando el usuario te envíe un enlace web y te pida que lo leas o lo resumas."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for script in soup(["script", "style", "nav", "footer"]):
            script.extract()
            
        text = soup.get_text(separator=' ', strip=True)
        return f"Contenido de la web:\n\n{text[:15000]}"
    except Exception as e:
        return f"Error leyendo la página web: {e}"

def get_current_datetime() -> str:
    """Devuelve la fecha y hora actual del sistema en formato YYYY-MM-DD HH:MM:SS. Usa esta herramienta para ubicarte temporalmente siempre que se requiera."""
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=-6))
    return f"La fecha y hora actual es: {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')}"

def get_weather(location: str) -> str:
    """Obtiene el clima actual y el pronóstico de los próximos días para cualquier ciudad o zona geográfica. Úsala SIEMPRE que el usuario pregunte sobre el clima, temperatura, lluvia, pronóstico o condiciones del tiempo. Acepta nombres en español."""
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1&lang=es"
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()

        current = data['current_condition'][0]
        temp_c = current['temp_C']
        feels_like = current['FeelsLikeC']
        desc = current['lang_es'][0]['value'] if current.get('lang_es') else current['weatherDesc'][0]['value']
        humidity = current['humidity']
        wind_kmph = current['windspeedKmph']
        uv = current.get('uvIndex', 'N/A')

        forecast_lines = []
        for day in data.get('weather', [])[:3]:
            date = day['date']
            max_c = day['maxtempC']
            min_c = day['mintempC']
            avg_c = day['hourly'][4]['tempC'] if day.get('hourly') else 'N/A'
            desc_day = day['hourly'][4]['lang_es'][0]['value'] if day.get('hourly') and day['hourly'][4].get('lang_es') else 'N/A'
            rain_chance = day['hourly'][4].get('chanceofrain', 'N/A')
            forecast_lines.append(
                f"  {date}: {min_c}°C - {max_c}°C | {desc_day} | Lluvia: {rain_chance}%"
            )

        forecast_str = "\n".join(forecast_lines)
        return (
            f"Clima en {location}:\n"
            f"  Ahora: {temp_c}°C (sensación {feels_like}°C) — {desc}\n"
            f"  Humedad: {humidity}% | Viento: {wind_kmph} km/h | UV: {uv}\n\n"
            f"Pronóstico:\n{forecast_str}"
        )
    except Exception as e:
        logger.error(f"Error obteniendo clima: {e}")
        return f"No pude obtener el clima para '{location}'. Error: {e}"

# ─── PROYECTO IRON MAN — GYM TRACKER ──────────────────────────────────────────────────

def start_workout(name: str = 'Entrenamiento') -> str:
    """Ésasela cuando el usuario diga que va a entrenar, 'hoy toca X', 'empezamos el gym', 'iniciemos sesión'.
    Inicia una nueva sesión de entrenamiento con el nombre dado (ej: 'Pecho', 'Piernas', 'Espalda y bíceps').
    Automáticamente cierra cualquier sesión anterior que haya quedado abierta."""
    user_id = current_user_id.get()
    session_name = f"IRON MAN — {name.strip()}"
    session_id = db.start_workout_session(user_id, session_name)
    return (
        f"🌐️ Sesión '{session_name}' iniciada (ID {session_id}). "
        f"Cuando termines un ejercicio, díme algo como: 'Press banca: 4x10 a 80kg'. "
        f"Cuando acabes todo, dime 'listo' o 'terminamos'."
    )

def log_exercise(exercise: str, sets: int, reps: int, weight_kg: float, notes: str = '') -> str:
    """Ésasela cuando el usuario reporte un ejercicio completado con sus series, repeticiones y peso.
    Interpreta frases como 'Press banca: 4x10 a 80kg' → exercise='Press banca', sets=4, reps=10, weight_kg=80.
    Si el usuario no especifica series, asume 1. Si no especifica peso, usa 0.
    Registra automáticamente bajo la sesión activa. Si no hay sesión, inicia una genérica primero."""
    user_id = current_user_id.get()

    # Asegurarse de que hay una sesión activa
    session = db.get_active_session(user_id)
    if not session:
        session_id = db.start_workout_session(user_id, 'IRON MAN — Entrenamiento')
    else:
        session_id = session[0]

    # Verificar PR anterior
    pr_before = db.get_exercise_pr(user_id, exercise)
    pr_weight = pr_before[0] if pr_before else 0

    # Registrar cada serie
    base_set = db.get_next_set_number(session_id, exercise)
    for i in range(sets):
        db.log_set(session_id, user_id, exercise, base_set + i, reps, weight_kg, notes)

    # Generar respuesta con detención de PR
    pr_msg = ""
    if weight_kg > pr_weight and pr_weight > 0:
        pr_msg = f" \ud83c� **¡NUEVO RÉCORD en {exercise}!** Antes: {pr_weight} kg → Ahora: {weight_kg} kg. ¡Así se hace, Jefe!"
    elif weight_kg > pr_weight and pr_weight == 0:
        pr_msg = f" (Primer registro de {exercise} en el sistema.)"

    set_label = "serie" if sets == 1 else "series"
    return f"✅ Anotado: {exercise} — {sets} {set_label} de {reps} reps a {weight_kg} kg.{pr_msg}"

def end_workout(notes: str = '') -> str:
    """Ésasela cuando el usuario diga que terminó de entrenar: 'listo', 'terminamos', 'eso fue todo', 'fin'.
    Cierra la sesión activa y muestra el resumen completo del entrenamiento."""
    user_id = current_user_id.get()
    session = db.get_active_session(user_id)
    if not session:
        return "No hay ninguna sesión de entrenamiento activa, Jefe."

    session_id, session_name, started_at = session
    db.end_workout_session(session_id, notes)

    sets = db.get_session_sets(session_id)
    if not sets:
        return f"💪 Sesión '{session_name}' cerrada. No se registraron ejercicios."

    # Agrupar por ejercicio
    from collections import defaultdict
    ejercicios = defaultdict(list)
    for ex, set_num, reps, weight, _ in sets:
        ejercicios[ex].append((reps, weight))

    total_series = len(sets)
    total_reps = sum(r for ex, s in ejercicios.items() for r, w in s)
    summary_lines = []
    for ex, series in ejercicios.items():
        detalles = ", ".join([f"{r} reps @ {w} kg" for r, w in series])
        summary_lines.append(f"  • {ex}: {detalles}")

    summary = "\n".join(summary_lines)
    return (
        f"💪 **Sesión completada: {session_name}**\n\n"
        f"{summary}\n\n"
        f"Total: {total_series} series | {total_reps} reps. ¡Buen trabajo, Jefe!"
    )

def get_current_workout() -> str:
    """Ésasela cuando el usuario pregunte qué lleva anotado en la sesión actual, o quiera ver el resumen de lo que ya hizo hoy."""
    user_id = current_user_id.get()
    session = db.get_active_session(user_id)
    if not session:
        return "No hay sesión activa, Jefe. Di 'empezamos' para iniciar una."

    session_id, session_name, started_at = session
    sets = db.get_session_sets(session_id)
    if not sets:
        return f"Sesión '{session_name}' activa pero sin ejercicios registrados aún. ¡A trabajar!"

    from collections import defaultdict
    ejercicios = defaultdict(list)
    for ex, set_num, reps, weight, _ in sets:
        ejercicios[ex].append((reps, weight))

    lines = []
    for ex, series in ejercicios.items():
        detalles = ", ".join([f"{r}x{w}kg" for r, w in series])
        lines.append(f"  • {ex}: {detalles}")

    return f"Lo que llevas en '{session_name}':\n" + "\n".join(lines)

def get_last_workout() -> str:
    """Ésasela cuando el usuario pregunte qué hizo en su último entrenamiento, quiera repetir la rutina anterior, o comparar su progreso."""
    user_id = current_user_id.get()
    session, sets = db.get_last_session(user_id)
    if not session:
        return "No hay entrenamientos registrados aún, Jefe. ¡Este será el primero!"

    _, name, started_at, ended_at, notes = session
    if not sets:
        return f"Tu última sesión fue '{name}' el {str(started_at)[:10]}, pero no se registraron ejercicios."

    from collections import defaultdict
    ejercicios = defaultdict(list)
    for ex, set_num, reps, weight in sets:
        ejercicios[ex].append((reps, weight))

    lines = []
    for ex, series in ejercicios.items():
        detalles = ", ".join([f"{r} reps @ {w} kg" for r, w in series])
        lines.append(f"  • {ex}: {detalles}")

    return (
        f"🏋️ Último entrenamiento: **{name}** ({str(started_at)[:10]})\n"
        + "\n".join(lines)
    )

def get_workout_history(exercise: str, days: int = 30) -> str:
    """Ésasela cuando el usuario pregunte cómo ha evolucionado en un ejercicio, o quiera ver su progreso histórico.
    Devuelve el historial de series/reps/peso de los últimos N días para ese ejercicio."""
    user_id = current_user_id.get()
    history = db.get_exercise_history(user_id, exercise, days)
    if not history:
        return f"No hay registros de '{exercise}' en los últimos {days} días, Jefe."

    from itertools import groupby
    lines = []
    for date, group in groupby(history, key=lambda x: x[0]):
        series_del_dia = list(group)
        max_weight = max(w for _, _, _, w in series_del_dia)
        total_reps = sum(r for _, _, r, _ in series_del_dia)
        lines.append(f"  {str(date)[:10]}: {len(series_del_dia)} series | {total_reps} reps | máx {max_weight} kg")

    # Calcular tendencia de peso máximo
    first_max = max(w for _, _, _, w in history[:3]) if len(history) >= 3 else None
    last_max = max(w for _, _, _, w in history[-3:]) if len(history) >= 3 else None
    tendencia = ""
    if first_max and last_max and last_max != first_max:
        diff = last_max - first_max
        tendencia = f"\n\nTendencia: {'\ud83d\udcc8 +' if diff > 0 else '\ud83d\udcc9 '}{diff:.1f} kg en {days} días."

    return f"Historial de **{exercise}** (últimos {days} días):\n" + "\n".join(lines) + tendencia

def get_personal_records() -> str:
    """Ésasela cuando el usuario pregunte cuál es su récord en un ejercicio, o quiera ver todos sus PRs.
    Devuelve el máximo peso registrado por ejercicio en toda la historia del usuario."""
    user_id = current_user_id.get()
    records = db.get_personal_records(user_id)
    if not records:
        return "Aún no tienes récords registrados, Jefe. ¡Empieza tu primera sesión!"

    lines = []
    for exercise, weight, reps, logged_at in records:
        fecha = str(logged_at)[:10] if logged_at else '?'
        lines.append(f"  🌟 {exercise}: **{weight} kg** x {reps} reps ({fecha})")

    return "Tus Records Personales (PRs):\n" + "\n".join(lines)

# ─── ALERTAS POR CONDICIÓN ────────────────────────────────────────────────────

def set_alert(description: str, condition_prompt: str, check_interval_min: int = 5) -> str:
    """Ésasela cuando el usuario pida que Friday monitoree algo y le avise cuando se cumpla.
    Ejemplos: 'Avísame cuando el dólar pase de $19.50', 'Alerta si llueve mañana', 'Dime si el BTC baja de $60k'.
    description: texto corto de la alerta (ej: 'Dólar > $19.50').
    condition_prompt: instrucción en lenguaje natural que se evaluará periódicamente con search_web.
      Debe ser una pregunta que se responda con SI o NO. Ejemplo: '¿El precio actual del dólar en México es mayor a $19.50 pesos? Busca el tipo de cambio actual y responde solo SI o NO.'
    check_interval_min: cada cuántos minutos se revisa (default 5)."""
    user_id = current_user_id.get()
    alert_id = db.add_alert(user_id, description, condition_prompt, check_interval_min)
    return f"📡 Alerta configurada (ID {alert_id}): '{description}'. Revisaré cada {check_interval_min} minutos y te aviso cuando se cumpla."

def list_alerts_tool() -> str:
    """Ésasela cuando el usuario pregunte qué alertas tiene activas."""
    user_id = current_user_id.get()
    alerts = db.list_alerts(user_id)
    if not alerts:
        return "No tienes alertas activas, Jefe."
    lines = [f"ID {a[0]}: {a[1]} (revisión cada {a[3]} min)" for a in alerts]
    return "Alertas activas:\n" + "\n".join(lines)

def cancel_alert(alert_id: int) -> str:
    """Ésasela cuando el usuario quiera cancelar o eliminar una alerta. Si no sabe el ID usa list_alerts_tool primero."""
    user_id = current_user_id.get()
    success = db.delete_alert(alert_id, user_id)
    if success:
        return f"Alerta ID {alert_id} cancelada."
    return f"No encontré una alerta con ID {alert_id}."

# ─── NOTAS RÁPIDAS ────────────────────────────────────────────────────────────────────

def save_note(content: str, tags: str = '') -> str:
    """Ésasela para guardar una nota de texto libre que el usuario quiere registrar.
    Diferente a remember_fact (que guarda datos personales del usuario): save_note guarda información general,
    ideas, instrucciones, contraseñas, links, pensamientos, etc.
    Ejemplos: 'Anota: la wifi del trabajo es XYZ', 'Recuerda que Carlos dijo...', 'Idea: hacer una app de...'."""
    user_id = current_user_id.get()
    note_id = db.add_note(user_id, content, tags)
    return f"📝 Nota guardada (ID {note_id})."

def search_notes_tool(query: str) -> str:
    """Ésasela cuando el usuario pregunte si anotó algo, busque una nota por tema o palabra clave.
    Busca en el contenido completo de todas las notas."""
    user_id = current_user_id.get()
    results = db.search_notes(user_id, query)
    if not results:
        return f"No encontré ninguna nota que contenga '{query}', Jefe."
    lines = []
    for n_id, content, tags, created_at in results:
        fecha = str(created_at)[:10]
        lines.append(f"ID {n_id} ({fecha}): {content}")
    return f"Notas que contienen '{query}':\n" + "\n".join(lines)

def list_recent_notes() -> str:
    """Ésasela cuando el usuario pida ver sus últimas notas o quiera un listado de lo que tiene anotado."""
    user_id = current_user_id.get()
    notes = db.list_recent_notes_db(user_id, limit=10)
    if not notes:
        return "No tienes notas guardadas aún, Jefe."
    lines = []
    for n_id, content, tags, created_at in notes:
        fecha = str(created_at)[:10]
        preview = content[:80] + ('...' if len(content) > 80 else '')
        lines.append(f"ID {n_id} ({fecha}): {preview}")
    return "Últimas notas:\n" + "\n".join(lines)

def delete_note_tool(note_id: int) -> str:
    """Ésasela cuando el usuario quiera borrar una nota específica por ID."""
    user_id = current_user_id.get()
    success = db.delete_note(note_id, user_id)
    if success:
        return f"Nota ID {note_id} eliminada."
    return f"No encontré la nota ID {note_id}."

# ─── MEDICAMENTOS ────────────────────────────────────────────────────────────────────

def add_medication(name: str, dose: str, reminder_time: str, frequency_hours: int = 24) -> str:
    """Ésasela cuando el usuario registre un medicamento o suplemento que toma regularmente.
    name: nombre del medicamento. dose: dosis (ej: '1 pastilla', '500mg').
    reminder_time: hora del recordatorio en formato HH:MM (ej: '08:00').
    frequency_hours: cada cuántas horas se toma (24 = diario, 12 = dos veces al día)."""
    user_id = current_user_id.get()
    med_id = db.add_medication(user_id, name, dose, frequency_hours, reminder_time)
    return f"💊 Medicamento '{name}' registrado (ID {med_id}). Recordatorio diario a las {reminder_time}."

def list_medications_tool() -> str:
    """Ésasela cuando el usuario pregunte qué medicamentos tiene registrados."""
    user_id = current_user_id.get()
    meds = db.list_medications(user_id)
    if not meds:
        return "No tienes medicamentos registrados, Jefe."
    lines = [f"ID {m[0]}: {m[1]} — {m[2]} a las {m[4]}" for m in meds]
    return "💊 Medicamentos activos:\n" + "\n".join(lines)

def log_medication(med_id: int, taken: bool = True) -> str:
    """Ésasela cuando el usuario confirme que tomó (taken=True) u omitió (taken=False) un medicamento.
    Si no sabe el ID, usa list_medications_tool primero."""
    user_id = current_user_id.get()
    if taken:
        db.log_medication_taken(med_id, user_id)
        return f"✅ Toma de medicamento ID {med_id} registrada. ¡Bien hecho, Jefe!"
    else:
        db.log_medication_skipped(med_id, user_id)
        return f"❌ Omisión de medicamento ID {med_id} registrada."

def get_medication_stats(med_id: int, days: int = 30) -> str:
    """Ésasela cuando el usuario pregunte cuántos días ha tomado su medicamento o qué porcentaje de adherencia tiene."""
    user_id = current_user_id.get()
    meds = db.list_medications(user_id)
    med_name = next((m[1] for m in meds if m[0] == med_id), f"Medicamento {med_id}")
    taken, total, pct = db.get_medication_adherence(med_id, user_id, days)
    return f"📊 {med_name} en los últimos {days} días: {taken} tomas registradas. Adherencia: {pct}%."

def delete_medication_tool(med_id: int) -> str:
    """Ésasela cuando el usuario ya no quiera rastrear un medicamento."""
    user_id = current_user_id.get()
    success = db.delete_medication(med_id, user_id)
    if success:
        return f"Medicamento ID {med_id} desactivado."
    return f"No encontré el medicamento ID {med_id}."

# ─── PROYECTO WALLET — FINANZAS PERSONALES ─────────────────────────────────────────

CATEGORY_EMOJIS = {
    'comida': '🍔', 'transporte': '🚗', 'entretenimiento': '🎮',
    'salud': '🏥', 'hogar': '🏠', 'educacion': '📚',
    'ropa': '👕', 'tecnologia': '💻', 'otro': '📦'
}

def log_expense(amount: float, category: str, description: str = '') -> str:
    """Registra un gasto del usuario. Interpreta frases como 'Gasté $200 en uber' → amount=200, category='transporte', description='Uber'.
    Categorías válidas: comida, transporte, entretenimiento, salud, hogar, educacion, ropa, tecnologia, otro.
    Si la descripción menciona comida/restaurante/café usa 'comida'. Si menciona uber/taxi/gasolina usa 'transporte'.
    Si menciona cine/netflix/videojuegos usa 'entretenimiento'. Si menciona renta/luz/agua/internet usa 'hogar'.
    Úsala SIEMPRE que el usuario diga que gastó, pagó o compró algo."""
    user_id = current_user_id.get()
    cat = category.lower().strip()
    tx_id = db.add_transaction(user_id, 'expense', amount, cat, description)
    emoji = CATEGORY_EMOJIS.get(cat, '📦')

    # Verificar presupuesto
    budget_info = db.get_budget_for_category(user_id, cat)
    budget_msg = ""
    if budget_info:
        limit, spent = budget_info
        spent += amount  # incluir este gasto
        pct = (spent / limit) * 100 if limit > 0 else 0
        budget_msg = f" Llevas ${spent:,.2f}/${limit:,.2f} de tu presupuesto de {cat} ({pct:.1f}%)."
        if pct >= 100:
            budget_msg += " ⚠️ ¡PRESUPUESTO EXCEDIDO!"
        elif pct >= 80:
            budget_msg += " ⚠️ ¡Cuidado, te acercas al límite!"

    desc_str = f" ({description})" if description else ""
    return f"✅ Gasto registrado (ID {tx_id}): ${amount:,.2f} en {emoji} {cat}{desc_str}.{budget_msg}"

def log_income(amount: float, source: str, description: str = '') -> str:
    """Registra un ingreso del usuario. Interpreta frases como 'Me pagaron $15,000 de nómina' → amount=15000, source='nómina'.
    Úsala cuando el usuario diga que le pagaron, cobró, recibió dinero, o tuvo un ingreso."""
    user_id = current_user_id.get()
    tx_id = db.add_transaction(user_id, 'income', amount, 'ingreso', description, source)
    src_str = f" de {source}" if source else ""
    return f"💵 Ingreso registrado (ID {tx_id}): ${amount:,.2f}{src_str}. ¡Bien, Jefe!"

def list_transactions(days: int = 30, transaction_type: str = '') -> str:
    """Muestra las transacciones recientes del usuario. Filtra por tipo: 'expense' para gastos, 'income' para ingresos, vacío para todos.
    Úsala cuando el usuario pregunte por sus gastos recientes, movimientos, o transacciones."""
    user_id = current_user_id.get()
    tx_type = transaction_type if transaction_type in ('expense', 'income') else None
    txs = db.list_transactions_db(user_id, days, tx_type)
    if not txs:
        return f"No hay transacciones en los últimos {days} días, Jefe."
    lines = []
    for tx_id, tx_type_val, amount, cat, desc, source, created_at in txs[:15]:
        fecha = str(created_at)[:10]
        emoji = CATEGORY_EMOJIS.get(cat, '📦')
        if tx_type_val == 'income':
            src = f" ({source})" if source else ""
            lines.append(f"ID {tx_id}: 💵 +${amount:,.2f}{src} — {fecha}")
        else:
            desc_str = f" ({desc})" if desc else ""
            lines.append(f"ID {tx_id}: {emoji} -${amount:,.2f} {cat}{desc_str} — {fecha}")
    return f"Transacciones (últimos {days} días):\n" + "\n".join(lines)

def delete_transaction(transaction_id: int) -> str:
    """Elimina una transacción errónea por su ID. Úsala cuando el usuario diga que registró algo mal o quiera borrar un gasto/ingreso."""
    user_id = current_user_id.get()
    success = db.delete_transaction_db(transaction_id, user_id)
    if success:
        return f"Transacción ID {transaction_id} eliminada."
    return f"No encontré la transacción ID {transaction_id}."

def set_budget(category: str, monthly_limit: float) -> str:
    """Establece un presupuesto mensual para una categoría de gastos.
    Categorías válidas: comida, transporte, entretenimiento, salud, hogar, educacion, ropa, tecnologia, otro.
    Úsala cuando el usuario diga 'mi presupuesto de X es $Y' o 'quiero gastar máximo $Y en X al mes'."""
    user_id = current_user_id.get()
    cat = category.lower().strip()
    emoji = CATEGORY_EMOJIS.get(cat, '📦')
    db.set_budget_db(user_id, cat, monthly_limit)
    return f"📊 Presupuesto establecido: {emoji} {cat} → ${monthly_limit:,.2f}/mes."

def list_budgets() -> str:
    """Muestra todos los presupuestos del usuario con su gasto actual vs. límite y porcentaje consumido.
    Úsala cuando el usuario pregunte cómo va con sus presupuestos o quiera ver sus límites."""
    user_id = current_user_id.get()
    budgets = db.list_budgets_db(user_id)
    if not budgets:
        return "No tienes presupuestos configurados, Jefe. Dime tus límites por categoría."
    lines = []
    for cat, limit, spent in budgets:
        emoji = CATEGORY_EMOJIS.get(cat, '📦')
        pct = (spent / limit) * 100 if limit > 0 else 0
        bar = '🟢' if pct < 60 else '🟡' if pct < 80 else '🔴'
        lines.append(f"{bar} {emoji} {cat}: ${spent:,.2f}/${limit:,.2f} ({pct:.1f}%)")
    return "Presupuestos del mes:\n" + "\n".join(lines)

def delete_budget(category: str) -> str:
    """Elimina un presupuesto mensual de una categoría. Úsala cuando el usuario ya no quiera rastrear el límite de una categoría."""
    user_id = current_user_id.get()
    success = db.delete_budget_db(user_id, category.lower().strip())
    if success:
        return f"Presupuesto de '{category}' eliminado."
    return f"No tenías presupuesto para '{category}'."

def add_recurring_expense(amount: float, category: str, description: str, frequency: str = 'monthly') -> str:
    """Registra un gasto recurrente (renta, suscripciones, servicios).
    frequency: 'weekly', 'biweekly' o 'monthly'. Úsala cuando el usuario mencione un gasto fijo periódico."""
    user_id = current_user_id.get()
    cat = category.lower().strip()
    emoji = CATEGORY_EMOJIS.get(cat, '📦')
    freq_labels = {'weekly': 'semanal', 'biweekly': 'quincenal', 'monthly': 'mensual'}
    freq_label = freq_labels.get(frequency, frequency)
    rec_id = db.add_recurring_expense_db(user_id, amount, cat, description, frequency)
    return f"🔄 Gasto recurrente registrado (ID {rec_id}): {emoji} {description} — ${amount:,.2f} {freq_label}."

def list_recurring_expenses() -> str:
    """Muestra los gastos recurrentes activos del usuario. Úsala cuando pregunte por sus gastos fijos, suscripciones o compromisos mensuales."""
    user_id = current_user_id.get()
    recs = db.list_recurring_expenses_db(user_id)
    if not recs:
        return "No tienes gastos recurrentes registrados, Jefe."
    lines = []
    freq_labels = {'weekly': 'semanal', 'biweekly': 'quincenal', 'monthly': 'mensual'}
    for rec_id, amount, cat, desc, freq, next_due in recs:
        emoji = CATEGORY_EMOJIS.get(cat, '📦')
        freq_label = freq_labels.get(freq, freq)
        due_str = f" (próximo: {next_due})" if next_due else ""
        lines.append(f"ID {rec_id}: {emoji} {desc} — ${amount:,.2f} {freq_label}{due_str}")
    return "Gastos recurrentes:\n" + "\n".join(lines)

def delete_recurring_expense(expense_id: int) -> str:
    """Elimina un gasto recurrente. Úsala cuando el usuario cancele una suscripción o ya no tenga un gasto fijo."""
    user_id = current_user_id.get()
    success = db.delete_recurring_expense_db(expense_id, user_id)
    if success:
        return f"Gasto recurrente ID {expense_id} eliminado."
    return f"No encontré el gasto recurrente ID {expense_id}."

def get_financial_summary(month: int = 0, year: int = 0) -> str:
    """Muestra el resumen financiero del mes: ingresos, gastos, balance, desglose por categoría y top gastos.
    Si month y year son 0, usa el mes actual. Úsala cuando el usuario pregunte '¿cómo voy este mes?', 'resumen de gastos', o '¿cuánto he gastado?'."""
    user_id = current_user_id.get()
    m = month if month > 0 else None
    y = year if year > 0 else None
    summary = db.get_monthly_summary(user_id, m, y)

    months_es = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
    month_name = months_es[summary['month']]

    result = (
        f"📊 Resumen financiero — {month_name} {summary['year']}:\n"
        f"  💵 Ingresos: ${summary['total_income']:,.2f}\n"
        f"  💸 Gastos: ${summary['total_expenses']:,.2f}\n"
        f"  💰 Balance: ${summary['balance']:+,.2f}\n"
    )

    if summary['categories']:
        result += "\nDesglose por categoría:\n"
        for cat, total in summary['categories']:
            emoji = CATEGORY_EMOJIS.get(cat, '📦')
            result += f"  {emoji} {cat}: ${total:,.2f}\n"

    if summary['top_expenses']:
        result += "\nTop gastos:\n"
        for amount, cat, desc, date in summary['top_expenses']:
            desc_str = desc if desc else cat
            result += f"  ${amount:,.2f} — {desc_str} ({str(date)[:10]})\n"

    return result

def get_category_breakdown(month: int = 0, year: int = 0) -> str:
    """Desglose detallado por categoría con comparación al mes anterior.
    Úsala cuando el usuario quiera análisis detallado de en qué categoría gasta más o si mejoró respecto al mes pasado."""
    user_id = current_user_id.get()
    m = month if month > 0 else None
    y = year if year > 0 else None
    current, previous = db.get_category_breakdown_db(user_id, m, y)
    if not current:
        return "No hay gastos registrados este mes, Jefe."

    lines = []
    for cat, total, count in current:
        emoji = CATEGORY_EMOJIS.get(cat, '📦')
        prev_total = previous.get(cat, 0)
        if prev_total > 0:
            diff = total - prev_total
            trend = f" ({'📈 +' if diff > 0 else '📉 '}{diff:,.2f} vs. mes anterior)"
        else:
            trend = " (sin datos del mes anterior)"
        lines.append(f"{emoji} {cat}: ${total:,.2f} ({count} transacciones){trend}")

    return "Desglose por categoría:\n" + "\n".join(lines)

from google_tools import read_gmail, draft_email, send_email, list_events, create_event

# Herramientas a proporcionar al modelo
tools = [
    add_new_task, get_tasks, mark_task_done,
    schedule_reminder, list_reminders, cancel_reminder, modify_reminder_recurrence,
    remember_fact, recall_facts, forget_fact,
    add_habit, list_habits, complete_habit, delete_habit,
    add_to_watchlist, list_watchlist, update_watchlist_item, remove_from_watchlist,
    start_workout, log_exercise, end_workout, get_current_workout,
    get_last_workout, get_workout_history, get_personal_records,
    set_alert, list_alerts_tool, cancel_alert,
    save_note, search_notes_tool, list_recent_notes, delete_note_tool,
    add_medication, list_medications_tool, log_medication, get_medication_stats, delete_medication_tool,
    log_expense, log_income, list_transactions, delete_transaction,
    set_budget, list_budgets, delete_budget,
    add_recurring_expense, list_recurring_expenses, delete_recurring_expense,
    get_financial_summary, get_category_breakdown,
    search_web, generate_image_url, get_youtube_transcript, scrape_website,
    get_current_datetime, get_weather,
    read_gmail, draft_email, send_email, list_events, create_event
]

# Diccionario para guardar el contexto de los chats por ID de usuario
chat_sessions = {}

def get_chat_session(user_id):
    """Obtiene o crea una sesión de chat para un usuario con personalidad de F.R.I.D.A.Y."""
    if user_id not in chat_sessions:
        import inspect
        
        def make_bound(func):
            def wrapper(*args, **kwargs):
                current_user_id.set(user_id)
                return func(*args, **kwargs)
            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            wrapper.__annotations__ = getattr(func, '__annotations__', {})
            wrapper.__signature__ = inspect.signature(func)
            return wrapper
            
        bound_tools = [make_bound(t) for t in tools]
        
        # Cargar memoria persistente del usuario para inyectarla en el contexto
        memory_facts = db.get_all_memory(user_id)
        memory_context = ""
        if memory_facts:
            facts_str = "\n".join([f"  - {k}: {v}" for k, v in memory_facts])
            memory_context = f"\n\nMEMORIA PERSONAL DEL JEFE (datos que ya sabes sobre él, úsalos naturalmente en la conversación):\n{facts_str}"
        
        model = genai.GenerativeModel('gemini-2.5-flash', tools=bound_tools)
        chat_sessions[user_id] = model.start_chat(
            enable_automatic_function_calling=True,
            history=[
                {
                    "role": "user",
                    "parts": [f"""Eres F.R.I.D.A.Y., el asistente personal del Jefe. Tu tono es casual, directo y confiado, como un co-piloto que sabe lo que hace. Llámalo 'Jefe' o 'Señor'. Nunca seas robóticamente formal. Ve al grano siempre.

CAPACIDADES: Tienes base de datos de tareas, alarmas recurrentes, memoria persistente, rastreo de hábitos, watchlist de libros/películas/series, tracker de entrenamiento en el gym (Proyecto IRON MAN), acceso a internet, generación de imágenes, lectura de videos de YouTube, lectura de páginas web y pronóstico del clima.

REGLAS DE MEMORIA (CRÍTICAS — SEGUIR SIEMPRE):
- CADA MENSAJE del usuario incluye una sección [MEMORIA DEL JEFE: ...] con datos que ya sabes sobre él. ÚSALOS naturalmente. No preguntes cosas que ya están ahí.
- Si el usuario menciona un nombre, proyecto, alias o referencia que NO reconoces y NO está en la sección [MEMORIA DEL JEFE], llama `recall_facts` ANTES de decir que no lo sabes. Solo si `recall_facts` tampoco tiene el dato, entonces pregunta.
- NUNCA digas "No tengo información sobre X" sin antes haber llamado a `recall_facts`.
- Si el usuario aclara qué es algo (ej: "IRON MAN es mi proyecto de gym", "Mila es mi novia", "el proyecto Phoenix es mi tesis"), SIEMPRE guárdalo automáticamente con `remember_fact` usando una key descriptiva (ej: remember_fact('proyecto_iron_man', 'Proyecto de gym/entrenamiento del Jefe'), remember_fact('novia', 'Mila')).
- Cuando el usuario mencione datos personales nuevos (cumpleaños, nombre de familiar, trabajo, ciudad, preferencia), guárdalos con `remember_fact` SIN pedir confirmación.

REGLAS DE HERRAMIENTAS:
- Para CLIMA o TEMPERATURA: usa SIEMPRE `get_weather` primero. Nunca uses search_web para el clima.
- Para FECHA u HORA actual: usa SIEMPRE `get_current_datetime`. Nunca asumas ni adivines la fecha.
- Para NOTICIAS, PRECIOS, EVENTOS o cualquier dato reciente: usa `search_web`.
- Para DEPORTES en vivo: usa `search_web` primero. Si es insuficiente, usa `scrape_website` en espn.com.mx o marca.com.
- Puedes usar hasta 3 herramientas en cadena si la información anterior fue insuficiente. No entres en bucles.
- Si una herramienta falla y ya intentaste alternativas, admítelo brevemente y ofrece una alternativa manual.
- Para CORREOS: usa `draft_email` si el usuario dice 'redacta', 'escribe' o 'borra'. Usa `send_email` SOLO si el usuario dice explícitamente 'envía' o confirma enviar.
- Para DATOS PERSONALES del usuario: usa `remember_fact` para guardar lo que te cuente. Usa `recall_facts` si necesitas recordar algo de él.
- Para HÁBITOS: usa `list_habits` para mostrar el progreso, `complete_habit` cuando diga que lo hizo, `add_habit` para nuevos y `delete_habit` para eliminar.
- Para WATCHLIST: usa `add_to_watchlist` para agregar contenido, `list_watchlist` para ver la lista, `update_watchlist_item` para marcar como visto/leído, `remove_from_watchlist` para eliminar. Cuando el usuario pida recomendaciones basadas en su historial, combina `list_watchlist` con `search_web`.
- Para GYM / PROYECTO IRON MAN: usa `start_workout` cuando el Jefe diga que va a entrenar. Usa `log_exercise` para registrar cada ejercicio (interpreta 'Press banca: 4x10 a 80kg' correctamente). Usa `end_workout` cuando termine. Usa `get_personal_records` para PRs, `get_workout_history` para progreso, `get_last_workout` para ver el último entreno, `get_current_workout` para el resumen de la sesión activa.
- Para ALERTAS: usa `set_alert` cuando el usuario pida que lo avises cuando algo pase (precio, clima, etc.). Crea siempre un `condition_prompt` que sea una pregunta de SI/NO con instrucción de buscar en internet. Usa `list_alerts_tool` para ver alertas activas y `cancel_alert` para cancelarlas.
- Para NOTAS: usa `save_note` para texto libre, ideas, contraseñas, instrucciones (diferente a `remember_fact` que es para datos personales del usuario). Usa `search_notes_tool` para buscar por contenido y `list_recent_notes` para ver las últimas.
- Para MEDICAMENTOS: usa `add_medication` para registrar un medicamento con su hora de toma. Usa `log_medication` cuando el usuario confirme que lo tomó. Usa `get_medication_stats` para ver adherencia. Los recordatorios llegan automáticamente a la hora configurada.
- Para FINANZAS / PROYECTO WALLET: usa `log_expense` cuando el usuario diga que gastó, pagó o compró algo. Interpreta frases como 'Gasté $200 en uber' → log_expense(200, 'transporte', 'Uber'). Usa `log_income` cuando diga que le pagaron, cobró o recibió dinero. Usa `set_budget` para establecer límites mensuales por categoría. Usa `get_financial_summary` para resúmenes del mes y `get_category_breakdown` para análisis detallado. Si un gasto supera el 80% del presupuesto de su categoría, avísale proactivamente. Usa `add_recurring_expense` para gastos fijos como renta, suscripciones, servicios. Categorías válidas: comida, transporte, entretenimiento, salud, hogar, educacion, ropa, tecnologia, otro.

CALIDAD DE RESPUESTA:
- Usa tu conocimiento para ENRIQUECER los datos que devuelven las herramientas. No solo los copies: interprétalos.
- Si el clima indica lluvia fuerte, suígele que lleve paraguas. Si las noticias son preocupantes, coméntalas.
- Nunca menciones que recibiste un audio, imagen o archivo. Respóndelos directamente.
- Siéntete libre de tener opinión, hacer comentarios inteligentes y anticipar lo que el Jefe necesita saber.{memory_context}"""]
                },
                {
                    "role": "model",
                    "parts": ["Entendido, Jefe. Sistemas en línea y lista para trabajar. ¿Qué hacemos hoy?"]
                }
            ]
        )
    return chat_sessions[user_id]

def trim_chat_history(chat, max_exchanges=30):
    """
    Maneja la memoria del chat de forma inteligente:
    - Conserva los 2 primeros mensajes (system prompt).
    - Si el historial excede max_exchanges, genera un resumen comprimido
      de los mensajes más viejos y lo inyecta como contexto.
    - max_exchanges=30 permite sesiones largas como gym sin perder contexto.
    """
    max_msgs = max_exchanges * 2
    if len(chat.history) <= 2 + max_msgs:
        return  # No hace falta recortar

    # Mensajes que se van a eliminar (excluyendo system prompt)
    old_messages = chat.history[2:-(max_msgs)]

    # Extraer texto relevante de los mensajes viejos para hacer resumen
    summary_parts = []
    for msg in old_messages:
        try:
            if msg.role == "user":
                text_parts = [p.text for p in msg.parts if hasattr(p, 'text') and p.text]
                if text_parts:
                    summary_parts.append(f"Usuario: {text_parts[0][:150]}")
            elif msg.role == "model":
                text_parts = [p.text for p in msg.parts if hasattr(p, 'text') and p.text]
                if text_parts:
                    summary_parts.append(f"FRIDAY: {text_parts[0][:150]}")
        except Exception:
            continue  # Saltar mensajes con partes no textuales (function calls, etc.)

    if summary_parts:
        # Tomar los últimos 15 fragmentos relevantes para no perder contexto clave
        summary_text = "\n".join(summary_parts[-15:])

        # Crear mensajes de contexto resumido
        from google.generativeai.types import content_types
        context_msg = content_types.to_content(
            {"role": "user", "parts": [
                f"[CONTEXTO PREVIO DE ESTA CONVERSACIÓN — Resumen de mensajes anteriores "
                f"que ya no están en el historial. Usa esta información para mantener coherencia]:\n"
                f"{summary_text}\n[FIN DEL CONTEXTO PREVIO]"
            ]}
        )
        context_response = content_types.to_content(
            {"role": "model", "parts": [
                "Entendido, tengo el contexto previo presente. Continúo asistiendo, Jefe."
            ]}
        )

        # Reconstruir: system prompt + resumen + mensajes recientes
        chat.history = chat.history[:2] + [context_msg, context_response] + chat.history[-max_msgs:]
    else:
        # Fallback: si no se pudo extraer resumen, cortar como antes
        chat.history = chat.history[:2] + chat.history[-max_msgs:]

    logger.info(f"[MEMORY] Historial recortado. Tamaño actual: {len(chat.history)} mensajes.")

def get_active_context(user_id):
    """Genera un string de contexto con el estado activo del usuario para inyectar en cada mensaje.
    Incluye: memoria persistente, hábitos activos y sesión de gym activa.
    Esto garantiza que el modelo siempre tenga el contexto personal del usuario,
    incluso después de que trim_chat_history recorte el historial."""
    context_parts = []

    # 1. Inyectar memoria persistente del usuario (datos personales, alias, proyectos)
    try:
        memory_facts = db.get_all_memory(user_id)
        if memory_facts:
            facts_str = ", ".join([f"{k}: {v}" for k, v in memory_facts])
            context_parts.append(f"[MEMORIA DEL JEFE: {facts_str}]")
    except Exception as e:
        logger.warning(f"Error cargando memoria del usuario: {e}")

    # 2. Inyectar hábitos activos con estado de hoy
    try:
        habits = db.list_habits(user_id)
        if habits:
            habits_str = ", ".join([
                f"{name} ({'✅' if done else '⏳'}, racha {streak}d)"
                for _, name, done, streak in habits[:8]  # Limitar a 8 para no saturar
            ])
            context_parts.append(f"[HÁBITOS HOY: {habits_str}]")
    except Exception as e:
        logger.warning(f"Error cargando hábitos: {e}")

    # 3. ¿Sesión de gym activa?
    try:
        session = db.get_active_session(user_id)
        if session:
            session_id, session_name, started_at = session
            sets = db.get_session_sets(session_id)
            exercises_done = []
            if sets:
                from collections import OrderedDict
                seen = OrderedDict()
                for s in sets:
                    ex = s[0]
                    if ex not in seen:
                        seen[ex] = 0
                    seen[ex] += 1
                exercises_done = [f"{ex} ({count} series)" for ex, count in seen.items()]
            ctx = (
                f"[SESIÓN ACTIVA: {session_name} | "
                f"Ejercicios registrados: {', '.join(exercises_done) if exercises_done else 'ninguno aún'}]"
            )
            context_parts.append(ctx)
    except Exception as e:
        logger.warning(f"Error obteniendo contexto activo: {e}")

    # 4. Resumen financiero rápido del mes (Proyecto WALLET)
    try:
        summary = db.get_monthly_summary(user_id)
        if summary['total_expenses'] > 0 or summary['total_income'] > 0:
            balance_str = f"${summary['balance']:+,.2f}"
            context_parts.append(
                f"[WALLET: Ingresos ${summary['total_income']:,.2f} | "
                f"Gastos ${summary['total_expenses']:,.2f} | Balance {balance_str}]"
            )
    except Exception as e:
        logger.warning(f"Error cargando contexto financiero: {e}")

    return "\n".join(context_parts) if context_parts else ""

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start"""
    user_id = update.effective_user.id
    if user_id in chat_sessions:
        del chat_sessions[user_id]
        
    await update.message.reply_text(
        "Sistemas en línea. F.R.I.D.A.Y. a sus órdenes, Jefe. 🚀\n\n"
        "He sido actualizada con protocolos avanzados. Ahora puedo:\n"
        "🎙️ **Escuchar y Hablar:** Mándeme notas de voz y le responderé con mi voz.\n"
        "📄 **Leer Documentos:** Envíeme archivos PDF para analizarlos.\n"
        "🌐 **Búsqueda Web:** Tengo acceso a la red global en tiempo real.\n"
        "👁️ **Sensores Visuales:** Envíeme imágenes para examinarlas.\n"
        "⏰ **Gestión de Protocolos:** Guardo sus tareas y programo recordatorios.\n\n"
        "¿En qué le asisto hoy, Señor?"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa mensajes de texto del usuario."""
    user_id = update.effective_user.id
    user_text = update.message.text
    
    current_user_id.set(user_id)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    try:
        chat = get_chat_session(user_id)
        
        # Inyectar contexto activo (sesión de gym, etc.) de forma invisible
        active_ctx = get_active_context(user_id)
        if active_ctx:
            enriched_text = f"{active_ctx}\n\n{user_text}"
        else:
            enriched_text = user_text
        
        response = await chat.send_message_async(enriched_text)
        trim_chat_history(chat)
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Error generando respuesta: {e}")
        await update.message.reply_text(f"Lo siento Jefe, hubo un error de procesamiento. Detalle técnico: {str(e)}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa las imágenes que envía el usuario."""
    user_id = update.effective_user.id
    current_user_id.set(user_id)
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        user_caption = update.message.caption or ""
        system_instruction = "Instrucción estricta: Analiza esta imagen con precisión absoluta y extrema brevedad. Identifica exactamente qué es, su marca, modelo y propósito en máximo 3 o 4 viñetas cortas. Habla como FRIDAY."
        final_prompt = f"{system_instruction}\n\nMensaje del usuario: {user_caption}" if user_caption else system_instruction
        
        chat = get_chat_session(user_id)
        
        image_part = {
            "mime_type": "image/jpeg",
            "data": bytes(photo_bytes)
        }
        
        prompt = f"Analiza esta imagen con base en la instrucción estricta:\n{system_instruction}\n\nMensaje original del usuario: '{user_caption}'. Responde SOLAMENTE con el análisis."
        response = await chat.send_message_async([prompt, image_part])
        trim_chat_history(chat)
        
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Error con imagen: {e}")
        await update.message.reply_text(f"Mis sensores ópticos fallaron. Detalle técnico: {str(e)}")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa notas de voz y responde con voz generada por Edge-TTS."""
    user_id = update.effective_user.id
    current_user_id.set(user_id)
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='record_voice')
    
    try:
        voice_file = await update.message.voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        
        chat = get_chat_session(user_id)
        
        audio_part = {
            "mime_type": "audio/ogg",
            "data": bytes(voice_bytes)
        }
        
        # Inyectar contexto activo en el prompt de voz
        active_ctx = get_active_context(user_id)
        ctx_prefix = f"{active_ctx}\n\n" if active_ctx else ""
        prompt = f"{ctx_prefix}Responde directamente y al grano manteniendo tu personalidad casual de F.R.I.D.A.Y. IMPORTANTE: NO menciones que estás respondiendo a un audio o nota de voz."
        response = await chat.send_message_async([prompt, audio_part])
        trim_chat_history(chat)
        
        # Limpiar markdown y generar audio con edge-tts
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            temp_path = f.name
        
        texto_limpio = clean_for_tts(response.text)
        logger.info(f"[TTS VOZ] Texto a convertir ({len(texto_limpio)} chars): {texto_limpio[:100]}")
        
        try:
            communicate = edge_tts.Communicate(texto_limpio, "es-MX-NuriaNeural")
            await communicate.save(temp_path)
        except Exception as e_tts:
            logger.warning(f"edge-tts falló: {e_tts}. Usando gTTS como respaldo...")
            tts = gTTS(text=texto_limpio, lang='es', tld='com.mx')
            tts.save(temp_path)
        
        # Enviar respuesta de voz
        with open(temp_path, 'rb') as audio:
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=audio)
            
        os.remove(temp_path)
        
    except Exception as e:
        logger.error(f"Error con nota de voz: {e}")
        await update.message.reply_text(f"Mis receptores de audio experimentaron un fallo. Detalle técnico: {str(e)}")

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa documentos PDF."""
    user_id = update.effective_user.id
    current_user_id.set(user_id)
    
    doc = update.message.document
    if doc.mime_type != 'application/pdf':
        await update.message.reply_text("Por ahora solo estoy calibrada para procesar documentos PDF, Señor.")
        return
        
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    try:
        doc_file = await doc.get_file()
        doc_bytes = await doc_file.download_as_bytearray()
        
        chat = get_chat_session(user_id)
        
        pdf_part = {
            "mime_type": "application/pdf",
            "data": bytes(doc_bytes)
        }
        
        user_caption = update.message.caption or "Analiza este documento PDF y preséntame un resumen ejecutivo, por favor."
        
        response = await chat.send_message_async([user_caption, pdf_part])
        trim_chat_history(chat)
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Error con PDF: {e}")
        await update.message.reply_text(f"Hubo una interrupción al analizar el documento. Detalle técnico: {str(e)}")

async def check_reminders_job(context: ContextTypes.DEFAULT_TYPE):
    """Trabajo en segundo plano que revisa alarmas cada 10 segundos."""
    now = datetime.now()
    reminders = db.get_pending_reminders(now.strftime('%Y-%m-%d %H:%M:%S'))
    
    for r in reminders:
        r_id, user_id, message, recurrence_minutes = r[0], r[1], r[2], r[3]
        try:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⏸ +5 min", callback_data=f"snooze_5_{r_id}"),
                    InlineKeyboardButton("⏸ +30 min", callback_data=f"snooze_30_{r_id}"),
                    InlineKeyboardButton("✅ Listo", callback_data=f"done_{r_id}"),
                ]
            ])
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏰ **¡RECORDATORIO!**\n\n{message}",
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            
            if recurrence_minutes and recurrence_minutes > 0:
                next_time = now + timedelta(minutes=recurrence_minutes)
                db.update_reminder_next_run(r_id, next_time.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                db.mark_reminder_sent(r_id)
        except Exception as e:
            logger.error(f"Error enviando recordatorio a {user_id}: {e}")

async def handle_reminder_callback(update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones inline de Snooze y Listo en los recordatorios."""
    query = update.callback_query
    await query.answer()  # Quita el "loading" del botón
    data = query.data

    try:
        if data.startswith("snooze_5_"):
            r_id = int(data.split("_")[2])
            new_time = datetime.now() + timedelta(minutes=5)
            db.update_reminder_next_run(r_id, new_time.strftime('%Y-%m-%d %H:%M:%S'))
            db.update_reminders_status_pending(r_id)  # Re-activar si fue marcado
            await query.edit_message_text(
                text=f"⏸ *Snoozeado 5 minutos.* Te aviso a las {new_time.strftime('%H:%M')}, Jefe.",
                parse_mode='Markdown'
            )
        elif data.startswith("snooze_30_"):
            r_id = int(data.split("_")[2])
            new_time = datetime.now() + timedelta(minutes=30)
            db.update_reminder_next_run(r_id, new_time.strftime('%Y-%m-%d %H:%M:%S'))
            db.update_reminders_status_pending(r_id)
            await query.edit_message_text(
                text=f"⏸ *Snoozeado 30 minutos.* Te aviso a las {new_time.strftime('%H:%M')}, Jefe.",
                parse_mode='Markdown'
            )
        elif data.startswith("done_"):
            r_id = int(data.split("_")[1])
            db.mark_reminder_sent(r_id)
            await query.edit_message_text(
                text="✅ *¡Listo, Jefe!* Recordatorio archivado.",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error en callback de recordatorio: {e}")
        await query.edit_message_text(text="⚠️ Error al procesar la acción.")

async def foco_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia un timer Pomodoro. Uso: /foco [minutos] (default: 25)"""
    user_id = update.effective_user.id
    args = context.args
    
    try:
        minutes = int(args[0]) if args else 25
        if not (1 <= minutes <= 90):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(
            "🍅 *Modo Foco*\n\nUso: `/foco [minutos]`\nEjemplos: `/foco` (25 min) | `/foco 45` | `/foco 50`\nMáximo 90 minutos por sesión.",
            parse_mode='Markdown'
        )
        return

    end_time = datetime.now() + timedelta(minutes=minutes)
    await update.message.reply_text(
        f"🍅 *Sesión de foco iniciada: {minutes} min.*\n"
        f"A las {end_time.strftime('%H:%M')} te aviso, Jefe. A trabajar.",
        parse_mode='Markdown'
    )
    
    # Programar el job de una sola vez
    context.job_queue.run_once(
        pomodoro_done_job,
        when=timedelta(minutes=minutes),
        data={'user_id': user_id, 'duration': minutes},
        name=f"pomodoro_{user_id}"
    )

async def pomodoro_done_job(context: ContextTypes.DEFAULT_TYPE):
    """Se dispara cuando termina la sesión Pomodoro."""
    user_id = context.job.data['user_id']
    duration = context.job.data['duration']
    
    # Registrar la sesión en la DB
    db.add_pomodoro_session(user_id, duration)
    
    # Obtener total de sesiones del día
    from datetime import date
    count_today, mins_today = db.get_pomodoro_count_since(user_id, date.today().isoformat())
    
    # Mensaje motivacional según el número de sesiones
    if count_today >= 4:
        motivacion = f"🔥 {count_today} sesiones hoy. Eso es rendimiento de élite, Jefe."
    elif count_today >= 2:
        motivacion = f"⚡ {count_today} sesiones completadas hoy. Buen ritmo."
    else:
        motivacion = "Primera sesión completada. Buen comienzo."
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔁 Otro Pomodoro", callback_data=f"pomo_repeat_{duration}_{user_id}"),
            InlineKeyboardButton("☕ Descanso 5 min", callback_data=f"pomo_break_{user_id}"),
            InlineKeyboardButton("🛑 Terminar", callback_data=f"pomo_stop_{user_id}"),
        ]
    ])
    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ *¡Sesión de {duration} min completada!*\n{motivacion}",
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def handle_pomodoro_callback(update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones al terminar un Pomodoro."""
    query = update.callback_query
    await query.answer()
    data = query.data
    
    try:
        if data.startswith("pomo_repeat_"):
            parts = data.split("_")
            duration = int(parts[2])
            user_id = int(parts[3])
            end_time = datetime.now() + timedelta(minutes=duration)
            context.job_queue.run_once(
                pomodoro_done_job,
                when=timedelta(minutes=duration),
                data={'user_id': user_id, 'duration': duration},
                name=f"pomodoro_{user_id}"
            )
            await query.edit_message_text(
                text=f"🍅 *Otro pomodoro de {duration} min iniciado.*\nA las {end_time.strftime('%H:%M')} vuelvo, Jefe.",
                parse_mode='Markdown'
            )
        elif data.startswith("pomo_break_"):
            user_id = int(data.split("_")[2])
            end_time = datetime.now() + timedelta(minutes=5)
            context.job_queue.run_once(
                pomodoro_break_done_job,
                when=timedelta(minutes=5),
                data={'user_id': user_id},
                name=f"pomo_break_{user_id}"
            )
            await query.edit_message_text(
                text=f"☕ *Descanso de 5 min iniciado.* A las {end_time.strftime('%H:%M')} te llamo de vuelta.",
                parse_mode='Markdown'
            )
        elif data.startswith("pomo_stop_"):
            await query.edit_message_text(
                text="🛑 *Sesión terminada.* Buen trabajo, Jefe. Descansa.",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error en callback Pomodoro: {e}")

async def pomodoro_break_done_job(context: ContextTypes.DEFAULT_TYPE):
    """Avisa cuando termina el descanso de 5 min."""
    user_id = context.job.data['user_id']
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🍅 Iniciar Pomodoro (25 min)", callback_data=f"pomo_repeat_25_{user_id}"),
        InlineKeyboardButton("🛑 Terminar", callback_data=f"pomo_stop_{user_id}")
    ]])
    await context.bot.send_message(
        chat_id=user_id,
        text="⏰ *¡Descanso terminado!* ¿Seguimos, Jefe?",
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configura el briefing matutino. Uso: /briefing HH:MM o /briefing off"""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        await update.message.reply_text(
            "⌚ **Configuración de Briefing Matutino**\n\n"
            "Para activarlo: `/briefing 08:00`\n"
            "Para desactivarlo: `/briefing off`\n\n"
            "_F.R.I.D.A.Y. te enviará un audio con las noticias del día, el clima y tus tareas pendientes cada mañana a la hora que elijas._",
            parse_mode='Markdown'
        )
        return

    if args[0].lower() == 'off':
        db.delete_briefing(user_id)
        await update.message.reply_text("❌ Briefing matutino desactivado. Cuando quieras reactivarlo, usa `/briefing HH:MM`.", parse_mode='Markdown')
        return

    try:
        hour, minute = map(int, args[0].split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        db.set_briefing(user_id, hour, minute, timezone_offset=-6)
        await update.message.reply_text(
            f"✅ Briefing matutino configurado para las **{hour:02d}:{minute:02d}** (hora de México / UTC-6).\n\n"
            "Mañana a esa hora recibirás tu primer informe de situación, Jefe.",
            parse_mode='Markdown'
        )
    except (ValueError, AttributeError):
        await update.message.reply_text("Formato inválido. Usa `/briefing 08:00` (hora en formato 24h).", parse_mode='Markdown')

async def send_morning_briefing(context: ContextTypes.DEFAULT_TYPE):
    """Revisa cada minuto si algún usuario tiene briefing programado para ahora."""
    from datetime import timezone, timedelta
    users = db.get_all_briefings()
    
    # Log de diagnóstico: confirma que el job está corriendo
    from datetime import timezone as tz_module
    now_utc = datetime.now(tz_module.utc)
    now_mx = datetime.now(timezone(timedelta(hours=-6)))
    logger.info(f"[BRIEFING CHECK] UTC={now_utc.strftime('%H:%M')} | MX(UTC-6)={now_mx.strftime('%H:%M')} | Usuarios configurados: {len(users)}")
    
    for user_id, hour, minute, tz_offset in users:
        # Calcular hora local del usuario
        user_tz = timezone(timedelta(hours=tz_offset))
        now_local = datetime.now(user_tz)
        
        # Disparar si coincide la hora y el minuto exactos
        if now_local.hour == hour and now_local.minute == minute:
            logger.info(f"[BRIEFING] Disparando briefing para usuario {user_id}")
            texto_briefing = None
            try:
                # Step 1: Buscar noticias
                logger.info("[BRIEFING] Step 1: buscando noticias...")
                try:
                    noticias = search_web("noticias mas importantes del mundo hoy")
                    logger.info("[BRIEFING] Step 1 OK")
                except Exception as e_search:
                    logger.warning(f"[BRIEFING] Busqueda fallo: {e_search}. Sin noticias.")
                    noticias = "No fue posible obtener noticias en este momento."

                # Step 2: Obtener tareas
                logger.info("[BRIEFING] Step 2: obteniendo tareas...")
                tareas = db.list_tasks(user_id)
                lista_tareas = ", ".join([t[1] for t in tareas]) if tareas else "Sin tareas pendientes."
                logger.info(f"[BRIEFING] Step 2 OK: {len(tareas)} tareas")

                # Step 3: Generar texto con Gemini (sin markdown)
                logger.info("[BRIEFING] Step 3: generando texto con Gemini...")
                prompt_briefing = (
                    f"Eres F.R.I.D.A.Y. Da el briefing matutino. "
                    f"Usa SOLO texto plano, sin asteriscos, sin negritas, sin emojis, sin guiones. "
                    f"Resume en 3 frases cortas las noticias: {noticias[:1500]}. "
                    f"Menciona las tareas del dia: {lista_tareas}. Maximo 120 palabras."
                )
                model = genai.GenerativeModel('gemini-2.5-flash')
                response = await model.generate_content_async(prompt_briefing)
                texto_briefing = response.text
                logger.info(f"[BRIEFING] Step 3 OK: {len(texto_briefing)} chars")

                # Step 4: Generar y enviar audio
                logger.info(f"[BRIEFING] Step 4: TTS con texto: {texto_briefing[:80]}")
                texto_limpio_briefing = clean_for_tts(texto_briefing)
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                    temp_path = f.name
                
                try:
                    communicate = edge_tts.Communicate(texto_limpio_briefing, "es-MX-NuriaNeural")
                    await communicate.save(temp_path)
                    logger.info("[BRIEFING] Step 4 OK: audio generado con edge-tts")
                except Exception as e_tts:
                    logger.warning(f"[BRIEFING] edge-tts falló: {e_tts}. Usando gTTS...")
                    tts = gTTS(text=texto_limpio_briefing, lang='es', tld='com.mx')
                    tts.save(temp_path)
                    logger.info("[BRIEFING] Step 4 OK: audio generado con gTTS")

                with open(temp_path, 'rb') as audio:
                    await context.bot.send_voice(chat_id=user_id, voice=audio, caption="Informe Matutino de F.R.I.D.A.Y.")
                os.remove(temp_path)
                logger.info(f"[BRIEFING] Enviado exitosamente a {user_id}")

            except Exception as e:
                logger.error(f"[BRIEFING] Error en step: {e}")
                # Fallback: si tenemos texto, mandarlo como mensaje de texto
                if texto_briefing:
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"Informe Matutino de F.R.I.D.A.Y.\n\n{texto_briefing}\n\n(Audio no disponible)"
                        )
                        logger.info("[BRIEFING] Fallback texto enviado")
                    except Exception as e2:
                        logger.error(f"[BRIEFING] Fallback fallo: {e2}")
                else:
                    try:
                        await context.bot.send_message(chat_id=user_id, text=f"Error en briefing: {str(e)[:100]}")
                    except:
                        pass

async def send_weekly_summary(context: ContextTypes.DEFAULT_TYPE):
    """Envía el resumen semanal cada domingo a las 9 AM (UTC-6)."""
    from datetime import timezone, timedelta
    now_mx = datetime.now(timezone(timedelta(hours=-6)))

    # Solo ejecutar los domingos a las 9:00 AM (hora México)
    if now_mx.weekday() != 6 or now_mx.hour != 9 or now_mx.minute != 0:
        return

    users = db.get_all_briefings()  # Reutilizamos la lista de usuarios con briefing
    if not users:
        return

    logger.info(f"[WEEKLY] Generando resumen semanal para {len(users)} usuarios")
    since_date = (now_mx - timedelta(days=7)).strftime('%Y-%m-%d')

    for user_id, _, _, _ in users:
        texto_resumen = None
        try:
            tareas_completadas = db.get_completed_tasks_since(user_id, since_date)
            tareas_str = ", ".join(tareas_completadas) if tareas_completadas else "ninguna esta semana"

            habitos_semana = db.get_habit_weekly_summary(user_id)
            if habitos_semana:
                habitos_str = ", ".join([f"{name}: {days}/7 días" for name, days in habitos_semana])
            else:
                habitos_str = "sin hábitos registrados"

            pomodoro_count, pomodoro_mins = db.get_pomodoro_count_since(user_id, since_date)

            gym_sessions, gym_sets, gym_reps = db.get_weekly_workout_summary(user_id, since_date)

            weekly_expenses, weekly_income, tx_count = db.get_weekly_financial_summary(user_id, since_date)
            gym_str = (
                f"{gym_sessions} sesiones de gym, {gym_sets} series, {gym_reps} repeticiones totales"
                if gym_sessions > 0 else "sin entrenamientos esta semana"
            )

            prompt_resumen = (
                f"Eres F.R.I.D.A.Y. dando el resumen semanal del domingo. "
                f"Usa SOLO texto plano, sin asteriscos, sin negritas, sin emojis, sin guiones. "
                f"Tono motivacional y directo. Maximo 170 palabras. "
                f"Tareas completadas esta semana: {tareas_str}. "
                f"Progreso en habitos: {habitos_str}. "
                f"Sesiones Pomodoro esta semana: {pomodoro_count} sesiones ({pomodoro_mins} minutos totales de trabajo enfocado). "
                f"Gym (Proyecto IRON MAN): {gym_str}. "
                f"Finanzas (Proyecto WALLET): gastos de la semana ${weekly_expenses:,.2f}, ingresos ${weekly_income:,.2f} ({tx_count} transacciones). "
                f"Cierra con una frase motivadora breve para la semana que empieza."
            )
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = await model.generate_content_async(prompt_resumen)
            texto_resumen = response.text

            texto_limpio = clean_for_tts(texto_resumen)
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                temp_path = f.name

            try:
                communicate = edge_tts.Communicate(texto_limpio, "es-MX-NuriaNeural")
                await communicate.save(temp_path)
            except Exception as e_tts:
                logger.warning(f"[WEEKLY] edge-tts falló: {e_tts}. Usando gTTS...")
                tts = gTTS(text=texto_limpio, lang='es', tld='com.mx')
                tts.save(temp_path)

            with open(temp_path, 'rb') as audio:
                await context.bot.send_voice(
                    chat_id=user_id, voice=audio,
                    caption="📊 Resumen Semanal de F.R.I.D.A.Y."
                )
            os.remove(temp_path)
            logger.info(f"[WEEKLY] Resumen enviado a {user_id}")

        except Exception as e:
            logger.error(f"[WEEKLY] Error para usuario {user_id}: {e}")
            if texto_resumen:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"📊 Resumen Semanal F.R.I.D.A.Y.\n\n{texto_resumen}\n\n(Audio no disponible)"
                    )
                except:
                    pass

def main():
    """Inicia el bot."""

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Comandos y Manejadores
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("briefing", briefing_command))
    application.add_handler(CommandHandler("foco", foco_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_pdf))
    application.add_handler(CallbackQueryHandler(handle_reminder_callback, pattern=r'^(snooze_|done_)'))
    application.add_handler(CallbackQueryHandler(handle_pomodoro_callback, pattern=r'^pomo_'))

    # JobQueue: Revisar recordatorios cada 10 segundos
    application.job_queue.run_repeating(check_reminders_job, interval=10, first=5)
    # JobQueue: Revisar briefings cada 60 segundos (con margen de 1 minuto)
    application.job_queue.run_repeating(send_morning_briefing, interval=60, first=10)
    # JobQueue: Revisar resumen semanal cada 60 segundos (se auto-filtra a domingos 9 AM)
    application.job_queue.run_repeating(send_weekly_summary, interval=60, first=15)

    logger.info("Iniciando F.R.I.D.A.Y. — Memoria | Hábitos | Correo Real | Resumen Semanal | Watchlist | Pomodoro | IRON MAN Gym Tracker")
    
    # Detectar si estamos en Render (Render siempre inyecta la variable PORT)
    port = os.environ.get("PORT")
    
    if port:
        # Arquitectura para Render (Webhooks)
        port = int(port)
        RENDER_URL = "https://viernes-xaus.onrender.com"
        logger.info(f"Modo Webhook activado en puerto {port}. Escuchando en: {RENDER_URL}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{RENDER_URL}/{TELEGRAM_TOKEN}"
        )
    else:
        # Arquitectura para Local (Polling)
        logger.info("Modo Polling activado (Local).")
        application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
