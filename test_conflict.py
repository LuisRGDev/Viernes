import sys, time
from telegram.ext import ApplicationBuilder
async def err_h(u, c):
    print('CAUGHT ERROR', c.error)
app = ApplicationBuilder().token('8827603690:AAHvbCDn_m-6Sq5KjX6QymjKqsDNX-Vg8X0').build()
app.add_error_handler(err_h)
try:
    app.run_polling()
except Exception as e:
    print('EXCEPTION OUTSIDE:', e)
print('STILL ALIVE')
