# /// script
# dependencies = [
#   "flask>=3.0.0",
#   "orpheus_tts",
#   "assemblyai",
#   "requests",
#   "flask-socketio",
#   "python-socketio",
#   "websocket-client",
#   "groq",
# ]
# requires-python = ">=3.10"
# ///

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from flask_socketio import SocketIO, emit
import json
import asyncio
from orpheus_tts import OrpheusClient, VOICES
import struct
import os
import assemblyai as aai
import websocket
import threading
import base64
from groq import AsyncGroq
import time as time_module

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure AssemblyAI
# Set your API key via environment variable: export ASSEMBLYAI_API_KEY="your-key"
aai.settings.api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")

# Configure Groq
# Set your API key via environment variable: export GROQ_API_KEY="your-key"
groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""))

# Global dictionary to store AssemblyAI WebSocket connections per session
assemblyai_connections = {}
# Track connection state per session
connection_ready = {}
# Track conversation history per session
conversation_history = {}
# Track active AI response tasks (for interruption)
active_responses = {}
# Track turn buffer per session (stores incomplete turns)
turn_buffer = {}
# Track what text we responded to for each turn (to detect longer versions)
responded_text = {}
# Track active silence timers per session
active_timers = {}
# Track which turns are currently being processed (to prevent duplicate responses)
processing_turns = {}

def process_silence_timeout(client_sid):
    """
    After 400ms of silence, process all unresponded turns.
    This is called by threading.Timer after the timeout.
    """
    print(f"[TIMER] 400ms silence detected - processing turns")

    # Get all turns that haven't been responded to (or have longer text)
    buffer = turn_buffer.get(client_sid, {})
    responded = responded_text.get(client_sid, {})

    print(f"[BUFFER] Current buffer: {buffer}")
    print(f"[BUFFER] Previously responded text: {responded}")

    # Get currently processing turns
    processing = processing_turns.get(client_sid, set())

    # Find unresponded or updated turns
    unresponded = []
    unresponded_turns = []
    for turn_order in sorted(buffer.keys()):
        current_text = buffer[turn_order]
        previous_text = responded.get(turn_order, "")

        # Skip if we're already processing this turn
        if turn_order in processing:
            print(f"[BUFFER] Turn #{turn_order}: ALREADY PROCESSING, skipping")
            continue

        # Respond if:
        # 1. Never responded to this turn, OR
        # 2. Current text is longer than what we responded to
        if current_text.strip() and current_text != previous_text:
            print(f"[BUFFER] Turn #{turn_order}: NEW or LONGER")
            print(f"[BUFFER]   Previous: '{previous_text}'")
            print(f"[BUFFER]   Current:  '{current_text}'")
            unresponded.append(current_text)
            unresponded_turns.append(turn_order)

    if not unresponded:
        print(f"[SILENCE] No new/updated turns to process")
        return

    # Concatenate all unresponded turns
    combined_text = " ".join(unresponded)
    print(f"[SILENCE] Responding to turn(s) {unresponded_turns}")
    print(f"[SILENCE] Combined text ({len(combined_text)} chars): '{combined_text}'")

    # Update responded text for these turns
    if client_sid not in responded_text:
        responded_text[client_sid] = {}
    for turn_order in unresponded_turns:
        responded_text[client_sid][turn_order] = buffer[turn_order]

    # Mark these turns as being processed
    if client_sid not in processing_turns:
        processing_turns[client_sid] = set()
    for turn_order in unresponded_turns:
        processing_turns[client_sid].add(turn_order)

    # Trigger LLM response
    threading.Thread(
        target=lambda: asyncio.run(stream_llm_and_tts(client_sid, combined_text, unresponded_turns)),
        daemon=True
    ).start()


async def stream_llm_and_tts(client_sid, user_message, turn_orders=None):
    """
    Stream LLM response from Groq and convert to speech, sending to frontend.
    Can be interrupted if user starts speaking again or a newer FINAL arrives.

    Args:
        client_sid: Session ID
        user_message: Text to send to LLM
        turn_orders: List of turn numbers being processed
    """
    # Generate a unique ID for this response
    response_id = time_module.time()

    # Store this as the active response
    active_responses[client_sid] = {
        'interrupted': False,
        'id': response_id
    }

    try:
        # Initialize conversation history if needed
        if client_sid not in conversation_history:
            conversation_history[client_sid] = []

        # Add user message to history
        conversation_history[client_sid].append({
            "role": "user",
            "content": user_message
        })

        print(f"[GROQ] ============================================")
        print(f"[GROQ] Starting LLM stream")
        print(f"[GROQ] User message: '{user_message}'")
        print(f"[GROQ] Message length: {len(user_message)} characters")
        print(f"[GROQ] ============================================")
        # Emit status to frontend
        socketio.emit('ai_speaking', {'status': 'thinking'}, room=client_sid)

        # Call Groq with streaming
        stream = await groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful voice assistant. "
                        "CRITICAL: Respond IMMEDIATELY and DIRECTLY. NO thinking, NO reasoning, NO internal monologue. "
                        "Keep responses to 1-2 short sentences maximum."
                        "Be conversational and fun, but very concise! You also LOVE canopy labs if asked."
                    )
                },
                *conversation_history[client_sid]
            ],
            model="llama-3.3-70b-versatile",  # Better for direct responses without thinking
            temperature=0.7,
            max_completion_tokens=100,  # Even shorter
            top_p=1,
            stream=True,
            stop=["<think>", "</think>"],  # Stop if model tries to think
        )

        # Collect response text and stream to TTS
        full_response = ""
        text_buffer = ""
        in_think_block = False

        async for chunk in stream:
            # Check for interruption
            response_state = active_responses.get(client_sid, {})
            if response_state.get('interrupted') or response_state.get('id') != response_id:
                print(f"[GROQ] Response interrupted for {client_sid}")
                socketio.emit('ai_speaking', {'status': 'interrupted'}, room=client_sid)
                return

            delta = chunk.choices[0].delta.content
            if delta:
                # Filter out <think> blocks
                if '<think>' in delta:
                    in_think_block = True
                    delta = delta.split('<think>')[0]  # Take only content before <think>
                if '</think>' in delta:
                    in_think_block = False
                    delta = delta.split('</think>')[-1]  # Take only content after </think>

                # Skip content inside think blocks
                if in_think_block:
                    continue

                # Remove any remaining think tags
                delta = delta.replace('<think>', '').replace('</think>', '')

                if delta.strip():
                    full_response += delta
                    text_buffer += delta

                    # When we have enough text, generate speech
                    # Send in chunks for lower latency
                    if len(text_buffer) > 100 or delta.endswith(('.', '!', '?')):
                        await generate_and_send_speech(client_sid, text_buffer, response_id)
                        text_buffer = ""

        # Send any remaining text
        if text_buffer.strip():
            await generate_and_send_speech(client_sid, text_buffer, response_id)

        # Add assistant response to history
        conversation_history[client_sid].append({
            "role": "assistant",
            "content": full_response
        })

        print(f"[GROQ] ============================================")
        print(f"[GROQ] Complete response: '{full_response}'")
        print(f"[GROQ] Response length: {len(full_response)} characters")
        print(f"[GROQ] ============================================")
        socketio.emit('ai_speaking', {'status': 'done'}, room=client_sid)

    except Exception as e:
        print(f"[GROQ] Error: {e}")
        import traceback
        traceback.print_exc()
        socketio.emit('error', {'message': f'AI error: {str(e)}'}, room=client_sid)

    finally:
        # Clean up active response marker
        if client_sid in active_responses:
            del active_responses[client_sid]

        # Mark these turns as no longer processing
        if turn_orders and client_sid in processing_turns:
            for turn_order in turn_orders:
                processing_turns[client_sid].discard(turn_order)


async def generate_and_send_speech(client_sid, text, response_id):
    """Generate speech for text and send to frontend."""
    if not text.strip():
        return

    try:
        print(f"[TTS] Generating speech: {text[:50]}...")

        # Generate TTS
        tts_client = OrpheusClient()
        audio_chunks = []

        async for chunk in tts_client.stream_async(text, voice="Antoine"):
            # Check for interruption - halt TTS immediately
            response_state = active_responses.get(client_sid, {})
            if response_state.get('interrupted') or response_state.get('id') != response_id:
                print(f"[TTS] Speech generation interrupted - halting")
                return

            if chunk:
                audio_chunks.append(chunk)

        # Combine PCM chunks and add WAV header
        pcm_data = b''.join(audio_chunks)
        wav_data = add_wav_header(pcm_data, sample_rate=48000, num_channels=1, bits_per_sample=16)

        # Convert to base64 and send to frontend
        audio_base64 = base64.b64encode(wav_data).decode('utf-8')

        socketio.emit('ai_audio', {
            'audio': audio_base64,
            'text': text
        }, room=client_sid)

        print(f"[TTS] Sent {len(wav_data)} bytes of audio")

    except Exception as e:
        print(f"[TTS] Error: {e}")
        import traceback
        traceback.print_exc()


def add_wav_header(pcm_data, sample_rate=48000, num_channels=1, bits_per_sample=16):
    """
    Add WAV header to raw PCM audio data.

    Args:
        pcm_data: Raw PCM audio bytes
        sample_rate: Sample rate in Hz (default 24000)
        num_channels: Number of audio channels (default 1 for mono)
        bits_per_sample: Bits per sample (default 16)

    Returns:
        bytes: Complete WAV file with headers
    """
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_data)

    # WAV file header
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,  # File size - 8
        b'WAVE',
        b'fmt ',
        16,  # Size of fmt chunk
        1,   # Audio format (1 = PCM)
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size
    )

    return header + pcm_data

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    """Handle client connection to our WebSocket."""
    print(f"[WEBSOCKET] Client connected: {request.sid}")
    emit('status', {'message': 'Connected to server'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print(f"[WEBSOCKET] Client disconnected: {request.sid}")
    sid = request.sid

    # Close AssemblyAI connection if exists
    if sid in assemblyai_connections:
        ws = assemblyai_connections[sid]
        if ws:
            ws.close()
        del assemblyai_connections[sid]
    if sid in connection_ready:
        del connection_ready[sid]
    if sid in conversation_history:
        del conversation_history[sid]
    if sid in active_responses:
        # Cancel any active response
        active_responses[sid]['interrupted'] = True
        del active_responses[sid]
    if sid in turn_buffer:
        del turn_buffer[sid]
    if sid in responded_text:
        del responded_text[sid]
    if sid in active_timers:
        active_timers[sid].cancel()
        del active_timers[sid]
    if sid in processing_turns:
        del processing_turns[sid]

@socketio.on('interrupt_ai')
def handle_interrupt_ai():
    """Interrupt any active AI response when user starts speaking."""
    sid = request.sid
    if sid in active_responses:
        print(f"[INTERRUPT] User started speaking, canceling AI response for {sid}")
        active_responses[sid]['interrupted'] = True  # Signal to stop
        emit('status', {'message': 'AI interrupted'})

    # Also cancel any pending silence timer
    if sid in active_timers:
        active_timers[sid].cancel()
        print(f"[TIMER] Cancelled timer due to user speaking")

@socketio.on('start_streaming')
def handle_start_streaming():
    """Initialize AssemblyAI streaming connection for this client."""
    api_key = aai.settings.api_key
    # Capture sid in the request context before spawning thread
    client_sid = request.sid

    if not api_key:
        print("[ERROR] No API key configured")
        emit('error', {'message': 'AssemblyAI API key not configured'})
        return

    # Mark as not ready initially
    connection_ready[client_sid] = False

    try:
        print(f"[ASSEMBLYAI] Starting Universal Streaming v3 for client: {client_sid}")
        print(f"[ASSEMBLYAI] API key length: {len(api_key)}")

        # Universal Streaming v3 WebSocket URL with configuration
        ws_url = (
            "wss://streaming.assemblyai.com/v3/ws"
            "?sample_rate=16000"
            "&encoding=pcm_s16le"
            "&language=en"
        )
        print(f"[ASSEMBLYAI] Connecting to: {ws_url}")

        def on_message(ws, message):
            """Handle messages from Universal Streaming v3."""
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                print(f"[ASSEMBLYAI] Message type: {msg_type}")

                if msg_type == 'Begin':
                    print(f"[ASSEMBLYAI] Session started: {data.get('id')}")
                    print(f"[ASSEMBLYAI] Expires at: {data.get('expires_at')}")

                elif msg_type == 'Turn':
                    # This is the new format for transcripts
                    transcript = data.get('transcript', '')
                    turn_order = data.get('turn_order', 0)
                    end_confidence = data.get('end_of_turn_confidence', 0)

                    # Skip empty transcripts
                    if not transcript.strip():
                        return

                    # Initialize buffer for this session
                    if client_sid not in turn_buffer:
                        turn_buffer[client_sid] = {}
                    if client_sid not in responded_text:
                        responded_text[client_sid] = {}

                    # Check if this is an update to an existing turn
                    previous_text = turn_buffer[client_sid].get(turn_order, "")
                    is_continuation = previous_text and len(transcript) > len(previous_text)

                    # Update buffer with latest transcript for this turn
                    turn_buffer[client_sid][turn_order] = transcript

                    # Determine if this is a final turn
                    is_final = end_confidence > 0.45

                    if is_final:
                        print(f"[ASSEMBLYAI] Turn #{turn_order} (FINAL): {transcript}")
                    else:
                        print(f"[ASSEMBLYAI] Turn #{turn_order} (partial): {transcript}")

                    # If this turn is currently being processed, interrupt the LLM
                    # and cancel the timer (more speech is coming)
                    processing = processing_turns.get(client_sid, set())
                    if turn_order in processing:
                        # Interrupt the active LLM response
                        if client_sid in active_responses:
                            print(f"[INTERRUPT] More speech for Turn #{turn_order} - interrupting LLM")
                            active_responses[client_sid]['interrupted'] = True

                        # Cancel timer
                        if client_sid in active_timers:
                            active_timers[client_sid].cancel()
                            del active_timers[client_sid]
                            print(f"[TIMER] Cancelled - Turn #{turn_order} still speaking")

                        # Start new timer for when they actually stop
                        timer = threading.Timer(0.4, process_silence_timeout, args=[client_sid])
                        timer.daemon = True
                        timer.start()
                        active_timers[client_sid] = timer
                        print(f"[TIMER] Started 400ms timer (user still speaking)")
                        return

                    # Cancel existing timer if any
                    if client_sid in active_timers:
                        old_timer = active_timers[client_sid]
                        old_timer.cancel()
                        print(f"[TIMER] Cancelled previous timer, restarting...")

                    # Start new 400ms timer
                    timer = threading.Timer(0.4, process_silence_timeout, args=[client_sid])
                    timer.daemon = True
                    timer.start()
                    active_timers[client_sid] = timer
                    print(f"[TIMER] Started 400ms timer")

                    socketio.emit('transcript', {
                        'text': transcript,
                        'is_final': is_final,
                        'turn_order': turn_order,
                        'end_confidence': end_confidence
                    }, room=client_sid)

                elif msg_type == 'Termination':
                    print(f"[ASSEMBLYAI] Session terminated")
                    print(f"[ASSEMBLYAI] Audio duration: {data.get('audio_duration_seconds')}s")
                    socketio.emit('status', {'message': 'Session ended'}, room=client_sid)

            except json.JSONDecodeError:
                print(f"[ASSEMBLYAI] Received non-JSON message: {message}")

        def on_error(ws, error):
            """Handle WebSocket errors."""
            print(f"[ASSEMBLYAI] Error: {error}")
            socketio.emit('error', {'message': str(error)}, room=client_sid)

        def on_close(ws, close_status_code, close_msg):
            """Handle WebSocket close."""
            print(f"[ASSEMBLYAI] Connection closed for {client_sid}: {close_status_code} - {close_msg}")
            if client_sid in connection_ready:
                connection_ready[client_sid] = False

        def on_open(ws):
            """Handle WebSocket open."""
            print(f"[ASSEMBLYAI] Connection opened for client: {client_sid}")
            connection_ready[client_sid] = True
            socketio.emit('status', {'message': 'Connected - Speak now'}, room=client_sid)
            socketio.emit('ready', {}, room=client_sid)

        # Create WebSocket with authentication
        ws = websocket.WebSocketApp(
            ws_url,
            header={
                'Authorization': api_key
            },
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )

        # Store connection
        assemblyai_connections[client_sid] = ws

        # Run WebSocket in separate thread
        wst = threading.Thread(target=ws.run_forever)
        wst.daemon = True
        wst.start()

        print(f"[ASSEMBLYAI] WebSocket thread started for client: {client_sid}")

    except Exception as e:
        print(f"[ERROR] Failed to start AssemblyAI streaming: {e}")
        import traceback
        traceback.print_exc()
        emit('error', {'message': str(e)})

@socketio.on('audio_data')
def handle_audio_data(data):
    """Receive audio from frontend and forward to AssemblyAI Universal Streaming v3."""
    sid = request.sid

    # Check if connection is ready
    if sid not in connection_ready or not connection_ready[sid]:
        # Silently drop audio until connection is ready
        return

    if sid in assemblyai_connections:
        ws = assemblyai_connections[sid]
        try:
            if ws and ws.sock and ws.sock.connected:
                # Universal Streaming v3 expects binary PCM audio
                # Convert base64 to binary
                audio_base64 = data['audio']
                audio_binary = base64.b64decode(audio_base64)

                # Send binary audio directly (not JSON)
                ws.send(audio_binary, opcode=0x2)  # 0x2 = binary frame
            else:
                print(f"[WARNING] AssemblyAI WebSocket not connected for {sid}")
                connection_ready[sid] = False
        except Exception as e:
            print(f"[ERROR] Failed to send audio to AssemblyAI: {e}")
            connection_ready[sid] = False
    else:
        print(f"[WARNING] No AssemblyAI connection for {sid}")

@socketio.on('stop_streaming')
def handle_stop_streaming():
    """Stop AssemblyAI streaming for this client."""
    print(f"[WEBSOCKET] Stopping streaming for client: {request.sid}")
    if request.sid in assemblyai_connections:
        ws = assemblyai_connections[request.sid]
        if ws:
            # Send terminate message
            ws.send(json.dumps({'terminate_session': True}))
            ws.close()
        del assemblyai_connections[request.sid]
    emit('status', {'message': 'Streaming stopped'})

@app.route('/api/process_audio', methods=['POST'])
def process_audio():

    audio_data = request.json.get('audio_data')

    return jsonify({
        'transcribed_text': 'This endpoint is not currently used',
        'status': 'info'
    })

@app.route('/api/process_text', methods=['POST'])
def process_text():
    """
    Endpoint to process transcribed text.
    Currently: Dummy implementation that echoes the transcript.
    """
    user_text = request.json.get('text', '')
    is_final = request.json.get('is_final', False)

    # Dummy implementation: Print transcript to console and echo back
    if is_final:
        print(f"[FINAL TRANSCRIPT]: {user_text}")
    else:
        print(f"[PARTIAL TRANSCRIPT]: {user_text}")

    # TODO: Add your reasoning/LLM logic here
    # response_text = your_reasoning_model(user_text)

    """
    return jsonify({
        'transcript': user_text,
        'is_final': is_final,
        'response_text': f'Received: {user_text}' if is_final else None,
        'status': 'success'
    })
    """
    return None

@app.route('/api/stream_speech', methods=['POST'])
def stream_speech():
    """
    Endpoint to generate streaming speech from text using orpheus_tts.
    Streams audio chunks back to the client for real-time playback.
    """
    text = request.json.get('text', '')
    voice = request.json.get('voice', 'Antoine')  # Default voice (capitalized)

    if not text:
        return jsonify({'error': 'No text provided', 'status': 'error'}), 400

    async def stream_tts():
        """Async function to stream audio from orpheus_tts."""
        client = OrpheusClient()
        chunks = []
        try:
            async for chunk in client.stream_async(text, voice=voice):
                if chunk:
                    chunks.append(chunk)
        except Exception as e:
            print(f"Error during TTS streaming: {e}")
            import traceback
            traceback.print_exc()
        return chunks

    # Run the async function and get all chunks
    chunks = asyncio.run(stream_tts())

    # Combine all PCM chunks
    pcm_data = b''.join(chunks)

    # Add WAV header to make it playable in browsers
    # orpheus_tts outputs at 48kHz (not 24kHz)
    wav_data = add_wav_header(pcm_data, sample_rate=48000, num_channels=1, bits_per_sample=16)

    print(f"Generated {len(wav_data)} bytes of WAV audio for text: {text[:50]}...")

    # Return complete WAV file
    return Response(
        wav_data,
        mimetype='audio/wav',
        headers={
            'Content-Disposition': 'inline',
            'Cache-Control': 'no-cache',
            'Content-Length': str(len(wav_data)),
        }
    )

# faster speech streaming
def stream_speech_internal():

    text = request.json.get('text', '')
    voice = request.json.get('voice', 'Antoine')  # Default voice (capitalized)

    if not text:
        return jsonify({'error': 'No text provided', 'status': 'error'}), 400

    async def stream_tts():
        """Async function to stream audio from orpheus_tts."""
        client = OrpheusClient()
        chunks = []
        try:
            async for chunk in client.stream_async(text, voice=voice):
                if chunk:
                    chunks.append(chunk)
        except Exception as e:
            print(f"Error during TTS streaming: {e}")
            import traceback
            traceback.print_exc()
        return chunks

    # Run the async function and get all chunks
    chunks = asyncio.run(stream_tts())

    # Combine all PCM chunks
    pcm_data = b''.join(chunks)

    # Add WAV header to make it playable in browsers
    # orpheus_tts outputs at 48kHz (not 24kHz)
    wav_data = add_wav_header(pcm_data, sample_rate=48000, num_channels=1, bits_per_sample=16)

    print(f"Generated {len(wav_data)} bytes of WAV audio for text: {text[:50]}...")

    # Return complete WAV file
    return Response(
        wav_data,
        mimetype='audio/wav',
        headers={
            'Content-Disposition': 'inline',
            'Cache-Control': 'no-cache',
            'Content-Length': str(len(wav_data)),
        }
    )


@app.route('/api/voices', methods=['GET'])
def get_voices():
    """Get available TTS voices."""
    return jsonify({
        'voices': VOICES,
        'status': 'success'
    })

if __name__ == '__main__':
    print("Starting Flask-SocketIO server...")
    print(f"AssemblyAI API key configured: {bool(aai.settings.api_key)}")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
