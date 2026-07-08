import asyncio
import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

def get_current_datetime() -> str:
    """Devuelve la fecha y hora actual del sistema en formato YYYY-MM-DD HH:MM:SS. Usa esta herramienta para ubicarte temporalmente siempre que se requiera."""
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=-6))
    return f"La fecha y hora actual es: {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')}"

tools = [get_current_datetime]

model = genai.GenerativeModel('gemini-2.5-flash', tools=tools)
chat = model.start_chat(
    enable_automatic_function_calling=True,
    history=[
        {
            "role": "user",
            "parts": ["Adopta la personalidad de F.R.I.D.A.Y..."]
        },
        {
            "role": "model",
            "parts": ["Entendido, Jefe. Sistemas en línea y lista para trabajar. ¿Qué hacemos hoy?"]
        }
    ]
)

async def run():
    print("Sending message...")
    response = await chat.send_message_async('Que día es hoy?')
    print(f"Finish reason: {response.candidates[0].finish_reason}")
    print(f"Parts: {response.parts}")
    try:
        print(f"Text: {response.text}")
    except Exception as e:
        print(f"Error accessing text: {e}")

asyncio.run(run())
