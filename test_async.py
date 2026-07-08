import asyncio
import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

def get_time() -> str:
    return '2026-06-18'

model = genai.GenerativeModel('gemini-2.5-flash', tools=[get_time])
chat = model.start_chat(enable_automatic_function_calling=True)

async def run():
    response = await chat.send_message_async('Que hora es? Usa get_time')
    print(response.text)

asyncio.run(run())
