from elevenlabs.client import ElevenLabs
from elevenlabs.play import play

from config import ELEVENLABS_API_KEY

elevenlabs = ElevenLabs(
  api_key=ELEVENLABS_API_KEY,
)

audio = elevenlabs.text_to_speech.convert(
    text="The first move is what sets everything in motion.",
    voice_id="L0Dsvb3SLTyegXwtm47J",
    model_id="eleven_multilingual_v2",
    output_format="mp3_44100_128",
)

def play_audio():
    play(audio)


