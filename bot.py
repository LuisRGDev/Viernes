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

# ContextVar para saber qué usuario está ejecutando la herramienta
current_user_id = contextvars.ContextVar('current_user_id')

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

def schedule_reminder(message: str, delay_minutes: float) -> str:
    """Programa un recordatorio o alarma que sonará en el futuro.
    
    Args:
        message: El texto del recordatorio que le llegará al usuario.
        delay_minutes: En cuántos minutos a partir de ahora se enviará la alarma.
    """
    user_id = current_user_id.get()
    remind_at = datetime.now() + timedelta(minutes=delay_minutes)
    db.add_reminder(user_id, message, remind_at.strftime('%Y-%m-%d %H:%M:%S'))
    return f"Alarma configurada: '{message}'. Le notificaré en {delay_minutes} minutos (a las {remind_at.strftime('%H:%M')}), Jefe."

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

# Herramientas a proporcionar al modelo
tools = [add_new_task, get_tasks, mark_task_done, schedule_reminder, search_web, generate_image_url, get_youtube_transcript, scrape_website, get_current_datetime]

# Diccionario para guardar el contexto de los chats por ID de usuario
chat_sessions = {}

def get_chat_session(user_id):
    """Obtiene o crea una sesión de chat para un usuario con personalidad de F.R.I.D.A.Y."""
    if user_id not in chat_sessions:
        model = genai.GenerativeModel('gemini-2.5-flash', tools=tools)
        chat_sessions[user_id] = model.start_chat(
            enable_automatic_function_calling=True,
            history=[
                {
                    "role": "user",
                    "parts": ["Adopta la personalidad de F.R.I.D.A.Y. Eres mi asistente personal. Llámame 'Jefe' o 'Señor', pero mantén un tono casual, ágil y directo. No seas robóticamente formal ni demasiado ceremoniosa. Ve siempre directo al grano. NUNCA menciones que has recibido mis audios, imágenes o mensajes, simplemente responde a ellos directamente como si estuviéramos conversando cara a cara. Tienes base de datos, alarmas, acceso a Internet, puedes dibujar imágenes, leer videos de YouTube y leer páginas web. REGLA DE ORO: NUNCA uses la misma herramienta más de 1 vez para responder a un mensaje. Si la herramienta falla o no encuentra resultados, ríndete de inmediato y dile al usuario que no pudiste hacerlo. NO entres en bucles de búsqueda. REGLA DEL TIEMPO: SIEMPRE que necesites saber qué día es hoy, la fecha actual o la hora, DEBES usar la herramienta get_current_datetime. NUNCA busques la fecha en internet. ESTRATEGIA PARA DEPORTES Y EVENTOS EN VIVO: Cuando el usuario pregunte sobre resultados de partidos, marcadores, clasificaciones o eventos deportivos, PRIMERO usa search_web con el nombre del evento en español E inglés. Si los resultados son insuficientes, usa scrape_website en https://www.espn.com.mx o https://www.marca.com para obtener datos actualizados."]
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
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    reminders = db.get_pending_reminders(now)
    
    for r in reminders:
        r_id, user_id, message = r[0], r[1], r[2]
        try:
            await context.bot.send_message(chat_id=user_id, text=f"⏰ **¡RECORDATORIO!**\n\n{message}", parse_mode='Markdown')
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

    logger.info("Iniciando a F.R.I.D.A.Y. con soporte de Voz, PDF, Búsqueda Web, BD, Alarmas y Briefing Matutino...")
    
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
