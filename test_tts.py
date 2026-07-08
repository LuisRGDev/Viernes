import edge_tts, asyncio
async def main():
    try:
        await edge_tts.Communicate('', 'es-MX-NuriaNeural').save('test.mp3')
    except Exception as e:
        print('CAUGHT:', repr(e))
asyncio.run(main())
