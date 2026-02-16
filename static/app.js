class VoiceApp {
    constructor() {
        this.micButton = document.getElementById('micButton');
        this.statusText = document.getElementById('statusText');
        this.testButton = document.getElementById('testButton');
        this.transcriptText = document.getElementById('transcriptText');

        this.isListening = false;
        this.isProcessing = false;
        this.isSpeaking = false;

        // Socket.IO connection to our backend
        this.socket = null;
        this.audioContext = null;
        this.processor = null;
        this.source = null;
        this.stream = null;

        // Audio playback queue to prevent overlapping
        this.isPlayingAudio = false;
        this.audioQueue = [];

        this.init();
    }

    init() {
        this.micButton.addEventListener('click', () => this.toggleListening());
        this.testButton.addEventListener('click', () => this.testSpeech());

        // Initialize Socket.IO connection
        this.initSocketIO();
    }

    initSocketIO() {
        this.socket = io();
        this.assemblyAIReady = false;

        this.socket.on('connect', () => {
            console.log('[SOCKET] Connected to server');
        });

        this.socket.on('disconnect', () => {
            console.log('[SOCKET] Disconnected from server');
            this.assemblyAIReady = false;
        });

        this.socket.on('ready', () => {
            console.log('[SOCKET] AssemblyAI ready to receive audio');
            this.assemblyAIReady = true;
        });

        this.socket.on('status', (data) => {
            console.log('[SOCKET] Status:', data.message);
            this.statusText.textContent = data.message;
        });

        this.socket.on('transcript', (data) => {
            console.log('[SOCKET] Transcript:', data.text, 'Final:', data.is_final);
            this.updateTranscript(data.text, data.is_final);
        });

        this.socket.on('error', (data) => {
            console.error('[SOCKET] Error:', data.message);
            this.statusText.textContent = `Error: ${data.message}`;
            this.assemblyAIReady = false;
        });

        this.socket.on('ai_speaking', (data) => {
            console.log('[SOCKET] AI speaking:', data.status);
            if (data.status === 'thinking') {
                this.statusText.textContent = 'AI is thinking...';
            } else if (data.status === 'done') {
                this.statusText.textContent = 'Ready';
            } else if (data.status === 'interrupted') {
                this.statusText.textContent = 'Interrupted';
            }
        });

        this.socket.on('ai_audio', async (data) => {
            console.log('[SOCKET] Received AI audio:', data.text.substring(0, 50));
            await this.playAIAudio(data.audio);
        });
    }

    async playAIAudio(audioBase64) {
        // Add to queue
        this.audioQueue.push(audioBase64);
        console.log(`[AUDIO] Added to queue (queue length: ${this.audioQueue.length})`);

        // If already playing, let the queue process it
        if (this.isPlayingAudio) {
            console.log('[AUDIO] Already playing audio, will play queued audio next');
            return;
        }

        // Start processing the queue
        await this.processAudioQueue();
    }

    async processAudioQueue() {
        // Process all queued audio chunks sequentially
        while (this.audioQueue.length > 0) {
            const audioBase64 = this.audioQueue.shift();
            this.isPlayingAudio = true;

            try {
                console.log(`[AUDIO] Playing audio (${this.audioQueue.length} remaining in queue)`);

                // Convert base64 to blob
                const audioData = atob(audioBase64);
                const audioArray = new Uint8Array(audioData.length);
                for (let i = 0; i < audioData.length; i++) {
                    audioArray[i] = audioData.charCodeAt(i);
                }
                const audioBlob = new Blob([audioArray], { type: 'audio/wav' });
                const audioUrl = URL.createObjectURL(audioBlob);

                // Play audio and wait for it to finish
                await new Promise((resolve, reject) => {
                    const audio = new Audio(audioUrl);

                    audio.onended = () => {
                        console.log('[AUDIO] Audio chunk finished');
                        URL.revokeObjectURL(audioUrl);
                        resolve();
                    };

                    audio.onerror = (e) => {
                        console.error('[AUDIO] Playback error:', e);
                        URL.revokeObjectURL(audioUrl);
                        reject(e);
                    };

                    audio.play().catch(reject);
                });

            } catch (error) {
                console.error('[AUDIO] Error playing AI audio:', error);
            }
        }

        // All audio finished
        this.isPlayingAudio = false;
        console.log('[AUDIO] Queue empty, playback complete');
    }

    updateTranscript(text, isFinal = false) {
        this.transcriptText.textContent = text || '...';
        if (isFinal) {
            this.transcriptText.classList.remove('partial');
            this.transcriptText.classList.add('final');
        } else {
            this.transcriptText.classList.remove('final');
            this.transcriptText.classList.add('partial');
        }
    }

    async testSpeech() {
        if (this.isSpeaking || this.isProcessing) {
            console.log('Already processing or speaking, skipping test');
            return;
        }

        console.log('Testing speech output...');
        this.testButton.disabled = true;

        try {
            await this.streamSpeechResponse("Oh, go on?");
        } catch (error) {
            console.error('Test speech failed:', error);
        } finally {
            this.testButton.disabled = false;
        }
    }

    async toggleListening() {
        if (this.isProcessing) return;

        if (this.isListening) {
            this.stopListening();
        } else {
            this.startListening();
        }
    }

    async startListening() {
        this.isListening = true;
        this.micButton.classList.add('listening');
        this.statusText.textContent = 'Connecting to AssemblyAI...';
        this.updateTranscript('Initializing...', false);
        this.assemblyAIReady = false;

        // Interrupt any active AI response
        this.socket.emit('interrupt_ai');

        // Clear any queued audio chunks
        this.audioQueue = [];
        console.log('[AUDIO] Cleared audio queue due to user speaking');

        // Tell backend to start AssemblyAI streaming
        this.socket.emit('start_streaming');

        // Wait a moment for connection to establish
        await new Promise(resolve => setTimeout(resolve, 500));

        // Start capturing and streaming audio
        await this.startAudioStreaming();
    }

    async startAudioStreaming() {
        try {
            // Get microphone access
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true
                }
            });

            // Set up audio context and processing
            this.audioContext = new AudioContext({ sampleRate: 16000 });
            this.source = this.audioContext.createMediaStreamSource(this.stream);
            this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);

            this.processor.onaudioprocess = (e) => {
                // Only send audio if we're listening AND AssemblyAI is ready
                if (this.isListening && this.socket && this.socket.connected && this.assemblyAIReady) {
                    const audioData = e.inputBuffer.getChannelData(0);
                    // Convert to base64 and send to backend
                    const pcmData = this.convertFloat32ToInt16(audioData);
                    const base64 = this.arrayBufferToBase64(pcmData);
                    this.socket.emit('audio_data', { audio: base64 });
                }
            };

            this.source.connect(this.processor);
            this.processor.connect(this.audioContext.destination);

            console.log('[AUDIO] Audio streaming started');
            this.statusText.textContent = 'Listening - Speak now';

        } catch (error) {
            console.error('[AUDIO] Error starting audio streaming:', error);
            this.statusText.textContent = 'Microphone access denied';
            this.stopListening();
        }
    }

    convertFloat32ToInt16(float32Array) {
        const int16Array = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16Array.buffer;
    }

    arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    stopListening() {
        this.isListening = false;
        this.micButton.classList.remove('listening');
        this.statusText.textContent = 'Stopping...';

        // Tell backend to stop streaming
        if (this.socket && this.socket.connected) {
            this.socket.emit('stop_streaming');
        }

        // Stop audio recording
        if (this.processor) {
            this.processor.disconnect();
            this.processor = null;
        }
        if (this.source) {
            this.source.disconnect();
            this.source = null;
        }
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }

        setTimeout(() => {
            this.statusText.textContent = 'Ready';
        }, 1000);
    }

    async processAudio(audioBlob) {
        // TODO: Replace with your voice detection logic
        // This is a placeholder endpoint

        const formData = new FormData();
        formData.append('audio', audioBlob);

        try {
            // Placeholder - send audio to backend for transcription
            const response = await fetch('/api/process_audio', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    audio_data: 'base64_encoded_audio_placeholder'
                })
            });

            const data = await response.json();

            // For now, use a placeholder text input
            const userText = prompt('Enter your message (placeholder for voice):');
            if (userText) {
                this.handleUserInput(userText);
            } else {
                this.statusText.textContent = 'Ready';
            }
        } catch (error) {
            console.error('Error processing audio:', error);
            this.statusText.textContent = 'Error processing audio';
            setTimeout(() => this.statusText.textContent = 'Ready', 2000);
        }
    }

    async handleUserInput(text) {
        // Set processing state
        this.isProcessing = true;
        this.micButton.classList.add('processing');
        this.statusText.textContent = 'Thinking...';

        try {
            // TODO: Replace with your reasoning/LLM logic
            const response = await fetch('/api/process_text', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ text })
            });

            const data = await response.json();
            const responseText = data.response_text;

            // Stream the response as speech
            await this.streamSpeechResponse(responseText);

        } catch (error) {
            console.error('Error processing text:', error);
            this.statusText.textContent = 'Error processing request';
            setTimeout(() => {
                this.statusText.textContent = 'Ready';
            }, 2000);
        } finally {
            this.isProcessing = false;
            this.micButton.classList.remove('processing');
        }
    }

    async streamSpeechResponse(text) {
        // Set speaking state
        this.isSpeaking = true;
        this.micButton.classList.add('speaking');
        this.statusText.textContent = 'Speaking...';

        try {
            // Stream audio from orpheus_tts backend
            const response = await fetch('/api/stream_speech', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    text: text,
                    voice: 'antoine'  // Can be customized
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            // Get the audio blob from the streamed response
            const audioBlob = await response.blob();

            // Create a URL for the audio blob
            const audioUrl = URL.createObjectURL(audioBlob);

            // Create and play the audio
            const audio = new Audio(audioUrl);

            // Listen for when audio finishes playing
            audio.onended = () => {
                this.isSpeaking = false;
                this.micButton.classList.remove('speaking');
                this.statusText.textContent = 'Ready';
                // Clean up the blob URL
                URL.revokeObjectURL(audioUrl);
            };

            audio.onerror = (e) => {
                console.error('Audio playback error:', e);
                this.isSpeaking = false;
                this.micButton.classList.remove('speaking');
                this.statusText.textContent = 'Error playing audio';
                URL.revokeObjectURL(audioUrl);
                setTimeout(() => this.statusText.textContent = 'Ready', 2000);
            };

            // Start playing the audio
            await audio.play();

        } catch (error) {
            console.error('Error streaming speech:', error);
            this.isSpeaking = false;
            this.micButton.classList.remove('speaking');
            this.statusText.textContent = 'Error generating speech';
            setTimeout(() => this.statusText.textContent = 'Ready', 2000);
        }
    }
}

// Initialize app when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new VoiceApp();
});
