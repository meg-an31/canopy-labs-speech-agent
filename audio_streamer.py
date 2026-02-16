"""
Audio streaming module for orpheus_tts.
WSL-compatible: generates audio and sends to browser for playback.
"""

import asyncio
from orpheus_tts import OrpheusClient, VOICES
import io


class AudioStreamer:
    """
    Handles streaming audio from orpheus_tts.
    WSL-compatible: collects audio chunks to send to browser.
    """

    def __init__(self):
        self.is_streaming = False
        self.audio_chunks = []

    async def stream_tts_async(self, text, voice="antoine"):
        """
        Stream TTS audio and collect chunks.

        Args:
            text: The text to convert to speech
            voice: The voice to use (default: "antoine")

        Returns:
            list: Audio chunks as bytes
        """
        self.is_streaming = True
        self.audio_chunks = []

        client = OrpheusClient()

        try:
            async for chunk in client.stream_async(text, voice=voice):
                if chunk:
                    self.audio_chunks.append(chunk)

        except Exception as e:
            print(f"Error during TTS streaming: {e}")
            raise

        finally:
            self.is_streaming = False

        return self.audio_chunks

    def get_audio_bytes(self):
        """Get all audio chunks combined as bytes."""
        return b''.join(self.audio_chunks)

    def clear(self):
        """Clear collected audio chunks."""
        self.audio_chunks = []


async def generate_speech_stream(text, voice="antoine"):
    """
    Generate speech and return audio chunks with WAV headers.
    WSL-compatible function.

    Args:
        text: The text to convert to speech
        voice: The voice to use

    Returns:
        bytes: Complete audio data with WAV headers

    Example:
        audio_data = await generate_speech_stream("Hello, how are you?", voice="antoine")
    """
    import struct

    def add_wav_header(pcm_data, sample_rate=48000, num_channels=1, bits_per_sample=16):
        """Add WAV header to raw PCM audio data."""
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        data_size = len(pcm_data)

        header = struct.pack(
            '<4sI4s4sIHHIIHH4sI',
            b'RIFF', 36 + data_size, b'WAVE', b'fmt ',
            16, 1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample,
            b'data', data_size
        )
        return header + pcm_data

    streamer = AudioStreamer()
    chunks = await streamer.stream_tts_async(text, voice=voice)
    pcm_data = b''.join(chunks)
    # orpheus_tts outputs at 48kHz
    return add_wav_header(pcm_data, sample_rate=48000)


# For direct testing
if __name__ == "__main__":
    async def test():
        print("Available voices:", VOICES)
        print("\nTesting audio streaming (WSL-compatible)...")

        audio_data = await generate_speech_stream(
            "This is a test of the streaming audio system.",
            voice="antoine"
        )

        print(f"Generated {len(audio_data)} bytes of audio")

        # Save to file to test on Windows
        with open("test_output.wav", "wb") as f:
            f.write(audio_data)
        print("Audio saved to test_output.wav")

    asyncio.run(test())
