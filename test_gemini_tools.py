import asyncio
import google.generativeai as genai
import os
from dotenv import load_dotenv
import inspect

load_dotenv()
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

import contextvars
current_user_id = contextvars.ContextVar('current_user_id')

def test_tool() -> str:
    """A test tool"""
    print("Inside test_tool!")
    try:
        val = current_user_id.get()
        print(f"Value: {val}")
    except Exception as e:
        print(f"Error: {e}")
    return "Tool finished"

def make_bound(func):
    def wrapper(*args, **kwargs):
        print("Inside wrapper!")
        current_user_id.set(999)
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    wrapper.__annotations__ = getattr(func, '__annotations__', {})
    wrapper.__signature__ = inspect.signature(func)
    return wrapper

bound_tools = [make_bound(test_tool)]

model = genai.GenerativeModel('gemini-2.5-flash', tools=bound_tools)
chat = model.start_chat(enable_automatic_function_calling=True)

async def main():
    print("Sending message...")
    response = await chat.send_message_async("Call the test tool")
    print(response.text)

asyncio.run(main())
