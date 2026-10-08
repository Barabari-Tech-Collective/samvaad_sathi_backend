with open('tests/unit_tests/test_elevenlabs_tts.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('def test_generate_tts_audio_joins_async_chunks(', 'def test_generate_tts_audio_joins_async_chunks(monkeypatch, ')
content = content.replace('def test_generate_tts_audio_missing_api_key(', 'def test_generate_tts_audio_missing_api_key(monkeypatch, ')
content = content.replace('async def generate_chunks():', 'monkeypatch.setattr("src.services.elevenlabs_tts.settings.MOCK_TTS", False)\n    async def generate_chunks():')
content = content.replace('    error = await generate_tts_audio("hello world")', '    monkeypatch.setattr("src.services.elevenlabs_tts.settings.MOCK_TTS", False)\n    error = await generate_tts_audio("hello world")')

with open('tests/unit_tests/test_elevenlabs_tts.py', 'w', encoding='utf-8') as f:
    f.write(content)
