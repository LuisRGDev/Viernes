import os
import logging
import tempfile
import html
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
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
from google_tools import read_gmail, draft_email, send_email, list_events, create_event

# Herramientas a proporcionar al modelo
tools = [
    add_new_task, get_tasks, mark_task_done,
    schedule_reminder, list_reminders, cancel_reminder, modify_reminder_recurrence,
    remember_fact, recall_facts, forget_fact,
    add_habit, list_habits, complete_habit, delete_habit,
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

CAPACIDADES: Tienes base de datos de tareas, alarmas recurrentes, memoria persistente de datos del usuario, rastreo de hábitos con racha, acceso a internet, generación de imágenes, lectura de videos de YouTube, lectura de páginas web y pronóstico del clima.

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

CALIDAD DE RESPUESTA:
- Usa tu conocimiento para ENRIQUECER los datos que devuelven las herramientas. No solo los copies: interprétalos.
- Si el clima indica lluvia fuerte, suígele que lleve paraguas. Si las noticias son preocupantes, coméntalas.
- Nunca menciones que recibiste un audio, imagen o archivo. Respóndelos directamente.
- Si el usuario te menciona algo personal (nombre de un familiar, un proyecto, una preferencia), guárdalo automáticamente con `remember_fact`.
- Siéntete libre de tener opinión, hacer comentarios inteligentes y anticipar lo que el Jefe necesita saber.{memory_context}"""]
                },
                {
                    "role": "model",
                    "parts": ["Entendido, Jefe. Sistemas en línea y lista para trabajar. ¿Qué hacemos hoy?"]
                }
            ]
        )
    return chat_sessions[user_id]

def trim_chat_history(chat, max_exchanges=10):
    """
    Limita la memoria del bot para no gastar demasiados tokens.
    Conserva los 2 primeros mensajes (System Prompt) y los últimos N intercambios.
    """
    max_msgs = max_exchanges * 2
    if len(chat.history) > 2 + max_msgs:
        chat.history = chat.history[:2] + chat.history[-max_msgs:]

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
        response = await chat.send_message_async(user_text)
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
        
        prompt = "Responde directamente y al grano manteniendo tu personalidad casual de F.R.I.D.A.Y. IMPORTANTE: NO menciones que estás respondiendo a un audio o nota de voz."
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
            await context.bot.send_message(chat_id=user_id, text=f"⏰ **¡RECORDATORIO!**\n\n{message}", parse_mode='Markdown')
            
            if recurrence_minutes and recurrence_minutes > 0:
                next_time = now + timedelta(minutes=recurrence_minutes)
                db.update_reminder_next_run(r_id, next_time.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                db.mark_reminder_sent(r_id)
        except Exception as e:
            logger.error(f"Error enviando recordatorio a {user_id}: {e}")

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

            prompt_resumen = (
                f"Eres F.R.I.D.A.Y. dando el resumen semanal del domingo. "
                f"Usa SOLO texto plano, sin asteriscos, sin negritas, sin emojis, sin guiones. "
                f"Tono motivacional y directo. Máximo 150 palabras. "
                f"Tareas completadas esta semana: {tareas_str}. "
                f"Progreso en hábitos: {habitos_str}. "
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_pdf))

    # JobQueue: Revisar recordatorios cada 10 segundos
    application.job_queue.run_repeating(check_reminders_job, interval=10, first=5)
    # JobQueue: Revisar briefings cada 60 segundos (con margen de 1 minuto)
    application.job_queue.run_repeating(send_morning_briefing, interval=60, first=10)
    # JobQueue: Revisar resumen semanal cada 60 segundos (se auto-filtra a domingos 9 AM)
    application.job_queue.run_repeating(send_weekly_summary, interval=60, first=15)

    logger.info("Iniciando a F.R.I.D.A.Y. con Memoria Persistente, Hábitos, Correo Real, Resumen Semanal...")
    
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
