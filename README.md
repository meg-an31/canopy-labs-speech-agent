# canopy labs take-home!

talk to this little robot using canopy labs' orpheus tts model! 

### setup on your own machine

you will need both a `GROQ_API_KEY` and an `ASSEMBLYAI_API_KEY`.

then just run:

```commandline
uv run app.py
```

to start the conversation, press the microphone button.

---

# implementation

## speech-to-text
I currently use AssemblyAI to stream the raw audio input from the browser to the speech to text model. 
This returns a stream of text, incrementally updating the system's understanding of what is being said.

AssemblyAI also provides a "probability of this being the end of a sentence", which I use to detect whether a 
message is FINAL. 

If a message is detected as FINAL, it is sent over to the LLM. However, if in the same turn the user continues 
speaking, this stream is halted, and the full text is sent over the next time a FINAL message is detected.

Furthermore, to reduce reliance on the FINAL detection, I use a 400ms timer after each speech detection which 
ensures that every message gets a response, even if it doesn't actually look like the end of a sentence. 

## thinking model

User input is generated in the backend, and all LLM processing happens server-side; groq allows you to stream in 
responses from their agents, and I use this to 
grab the text responses as 
soon as they 
are generated. once a single sentence ies complete, it's sent over to the TTS model to be processed, reducing 
latency, and all other sentences it genrates from then are buffered.

Prompted to be concise and use short sentences, while also sounding as human as possible.

## text-to-speech

Buffered in sentence-by-sentence directly from the backend, then pushed to the front end to play through the browser.
Both the text-to-speech and the thinking model can be interrupted by a new, longer FINAL message. 

## what else would i implement?

Currently there is no true interruptions system, the speech is just buffered and responded to later, which isn't 
ideal. 

I would also experiment with different ways of message-passing to reduce computation time (right now both 
websockets and API endpoints are used). I would be interested to 
play around with webassembly etc to see how much can be optimised to reduce latency.

I also think the system prompt is super important here, and I haven't played around with it at all. I've restricted 
it to short full sentences, which helps with audio streaming. 
