import os
import logging
import tempfile
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import contextvars
from duckduckgo_search import DDGS
import edge_tts

import db

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
        results = DDGS().text(query, max_results=4)
        if not results:
            return "No encontré datos en la red global."
        response = ""
        for r in results:
            response += f"Título: {r['title']}\nResumen: {r['body']}\n\n"
        return response
    except Exception as e:
        logger.error(f"Error en búsqueda web: {e}")
        return "Problema de conexión con la red global."

# Herramientas a proporcionar al modelo
tools = [add_new_task, get_tasks, mark_task_done, schedule_reminder, search_web]

# Diccionario para guardar el contexto de los chats por ID de usuario
chat_sessions = {}

def get_chat_session(user_id):
    """Obtiene o crea una sesión de chat para un usuario con personalidad de F.R.I.D.A.Y."""
    if user_id not in chat_sessions:
        model = genai.GenerativeModel('gemini-2.5-flash-lite', tools=tools)
        chat_sessions[user_id] = model.start_chat(
            enable_automatic_function_calling=True,
            history=[
                {
                    "role": "user",
                    "parts": ["Adopta la personalidad de F.R.I.D.A.Y. Eres mi asistente personal. Llámame 'Jefe' o 'Señor', pero mantén un tono casual, ágil y directo. No seas robóticamente formal ni demasiado ceremoniosa. Ve siempre directo al grano. NUNCA menciones que has recibido mis audios, imágenes o mensajes, simplemente responde a ellos directamente como si estuviéramos conversando cara a cara. Tienes base de datos, alarmas y acceso a Internet."]
                },
                {
                    "role": "model",
                    "parts": ["Entendido, Jefe. Sistemas en línea y lista para trabajar. ¿Qué hacemos hoy?"]
                }
            ]
        )
    return chat_sessions[user_id]

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
        response = chat.send_message(user_text)
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Error generando respuesta: {e}")
        await update.message.reply_text("Lo siento Jefe, hubo un error de procesamiento.")

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
        
        response = chat.send_message([final_prompt, image_part])
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Error con imagen: {e}")
        await update.message.reply_text("Mis sensores ópticos fallaron. No pude procesar la imagen, Señor.")

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
        response = chat.send_message([prompt, audio_part])
        
        # Generar audio con edge-tts (Voz Femenina Mexicana - Dalia)
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            temp_path = f.name
        
        communicate = edge_tts.Communicate(response.text, "es-MX-DaliaNeural")
        await communicate.save(temp_path)
        
        # Enviar respuesta de voz
        with open(temp_path, 'rb') as audio:
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=audio)
            
        os.remove(temp_path)
        
    except Exception as e:
        logger.error(f"Error con nota de voz: {e}")
        await update.message.reply_text("Mis receptores de audio experimentaron un fallo, Jefe.")

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
        
        response = chat.send_message([user_caption, pdf_part])
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Error con PDF: {e}")
        await update.message.reply_text("Hubo una interrupción al analizar el documento, Señor.")

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

def main():
    """Inicia el bot."""
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Comandos y Manejadores
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_pdf))

    # JobQueue: Revisar recordatorios cada 10 segundos
    application.job_queue.run_repeating(check_reminders_job, interval=10, first=5)

    logger.info("Iniciando a F.R.I.D.A.Y. con soporte de Voz, PDF, Búsqueda Web, BD y Alarmas...")
    application.run_polling()

if __name__ == '__main__':
    main()
