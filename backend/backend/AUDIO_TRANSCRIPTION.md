# Audio Transcription

The audio transcription endpoint accepts a user's recorded answer, validates the file, transcribes it with OpenAI Whisper, stores transcription metadata on the matching question attempt, and deletes the temporary file.

## Endpoint

`POST /api/transcribe-whisper`

Authentication: required.

Multipart form fields:

- `file`: MP3, WAV, M4A, or FLAC audio file
- `question_attempt_id`: integer ID for an attempt owned by the authenticated user
- `language`: optional language code, defaults to `en`

Example:

```bash
curl -X POST "http://localhost:8000/api/transcribe-whisper" \
  -H "Authorization: Bearer $TOKEN" \
  -F "question_attempt_id=123" \
  -F "language=en" \
  -F "file=@answer.mp3"
```

Response shape:

```json
{
  "question_attempt_id": 123,
  "filename": "answer.mp3",
  "content_type": "audio/mpeg",
  "size": 1155720,
  "duration_seconds": 72.23,
  "audio_url": "qa_123_abcd1234ef567890.mp3",
  "transcription": {
    "text": "Transcribed answer text..."
  },
  "word_count": 130,
  "whisper_model": "whisper-1",
  "whisper_latency_ms": 6831,
  "whisper_error": null,
  "message": "Audio uploaded and transcribed successfully",
  "saved": true,
  "save_error": null,
  "follow_up_generated": true,
  "follow_up_metadata": {},
  "follow_up_question": {}
}
```

## Current Behavior

- Validates content type and file extension.
- Enforces a 25 MB limit and a 10-minute duration cap in `src/services/audio_processor.py`.
- Performs basic file-header validation.
- Sends audio bytes to OpenAI Whisper through `src/services/whisper.py`.
- Stores transcription JSON on `question_attempt.transcription`.
- Stores a generated reference name in `question_attempt.audio_url`.
- Deletes the temporary local file after processing.
- May trigger follow-up question generation through `src/services/follow_up.py`.

## What Is Not Implemented

- No permanent local audio storage.
- No S3, MinIO, or other object-storage upload.
- No FFmpeg normalization step.
- No custom or fine-tuned ASR model.

## Implementation Files

- `src/api/routes/audio.py`: route and ownership checks
- `src/models/schemas/audio.py`: response schema
- `src/services/audio_processor.py`: validation, duration estimate, temp file lifecycle, simple audio features
- `src/services/whisper.py`: OpenAI Whisper integration and transcription helpers
- `src/services/follow_up.py`: follow-up generation after successful transcription
- `src/repository/crud/question.py`: question-attempt persistence
