import asyncio
from orpheus_tts import OrpheusClient, VOICES
import io
import struct

# Global audio buffer to collect chunks
audio_chunks = []

def add_wav_header(pcm_data, sample_rate=48000, num_channels=1, bits_per_sample=16):
    """Add WAV header to raw PCM audio data."""
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_data)

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16, 1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample,
        b'data',
        data_size
    )
    return header + pcm_data

async def process_audio_async(chunk):

    if chunk:
        audio_chunks.append(chunk)
        # Show progress
        print(f"Received chunk: {len(chunk)} bytes")

async def main():
    global audio_chunks
    client = OrpheusClient()
    print("Available voices:", VOICES)

    try:
        print("\nStreaming speech from orpheus_tts...")
        print("(Note: In WSL, audio is collected but not played)")
        print("Use the web app to hear the audio in browser\n")

        # Stream audio chunks
        async for chunk in client.stream_async("Async streaming! Hi, how are you?", voice="antoine"):
            await process_audio_async(chunk)

        print(f"\nStreaming complete!")
        print(f"Total chunks received: {len(audio_chunks)}")
        print(f"Total audio size: {sum(len(c) for c in audio_chunks)} bytes")

        # Save to file with proper WAV headers
        if audio_chunks:
            output_file = "test_output.wav"
            pcm_data = b''.join(audio_chunks)
            # orpheus_tts outputs at 48kHz
            wav_data = add_wav_header(pcm_data, sample_rate=48000)
            with open(output_file, 'wb') as f:
                f.write(wav_data)
            print(f"Audio saved to {output_file} with WAV headers (playable on Windows)")

    except Exception as e:
        print(f"Error during streaming: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())