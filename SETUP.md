# AssemblyAI Universal Streaming v3 Setup

## Get Your API Key

1. Sign up at https://www.assemblyai.com/
2. Get your API key from the dashboard
3. Set it as an environment variable

## Setup (Required)

Set your API keys as environment variables:

```bash
export ASSEMBLYAI_API_KEY="your-assemblyai-key-here"
export GROQ_API_KEY="your-groq-key-here"
```

Get keys from:
- AssemblyAI: https://www.assemblyai.com/
- Groq: https://console.groq.com/

Then run the app:

```bash
uv run app.py
```

## API Version: Universal Streaming v3

This app uses the **new Universal Streaming v3 API**:
- WebSocket URL: `wss://streaming.assemblyai.com/v3/ws`
- Audio format: Binary PCM (16-bit, 16kHz, mono)
- Message types: `Begin`, `Turn`, `Termination`

## Architecture (Backend Streaming)

**All streaming now happens on the backend** to avoid CORS issues and use the new Universal Streaming API:

```
Browser → Socket.IO → Backend → AssemblyAI WebSocket
Browser ← Socket.IO ← Backend ← AssemblyAI WebSocket
```

### Flow:

1. **Browser** connects to backend via Socket.IO
2. **Browser** sends audio → Backend via Socket.IO
3. **Backend** forwards audio → AssemblyAI WebSocket
4. **AssemblyAI** sends transcripts → Backend
5. **Backend** forwards transcripts → Browser via Socket.IO
6. **Browser** displays transcript and sends to `process_text()`

### Benefits:

- ✅ No CORS issues
- ✅ API key stays secure on backend
- ✅ Uses new Universal Streaming API
- ✅ Backend controls the streaming session

## How It Works

1. **Click microphone** → Browser connects to AssemblyAI WebSocket
2. **Speak** → Audio streams to AssemblyAI in real-time
3. **Partial transcripts** → Displayed as you speak (gray/italic)
4. **Final transcripts** → Sent to `/api/process_text` when complete
5. **process_text()** → Currently prints transcript to console (dummy implementation)

## Features

- **Real-time streaming:** See transcription as you speak
- **Partial updates:** Text appears word-by-word
- **Final transcripts:** Sent to backend for processing
- **Visual feedback:** Transcript box shows current text

## Current Flow

```
Microphone → AssemblyAI WebSocket → Partial Transcript → Display
                                   → Final Transcript → process_text() → Print to console
```

## Next Steps

Replace the dummy `process_text()` implementation with your actual LLM/reasoning logic.
