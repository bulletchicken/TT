import pvporcupine
import pyaudio
import struct
from STT import test_stt

porcupine = pvporcupine.create(
  access_key='FoA8qWCr7lDTZNh1ZN3NkwDfT1O5Q1CzSXTsAngYKA+1j/ll7+gFUg==',
  keyword_paths=['wake_model_hey_ted.ppn']
)

paud = pyaudio.PyAudio()
audio_stream = paud.open(
  rate=porcupine.sample_rate, 
  channels=1, 
  format=pyaudio.paInt16, 
  input=True,
  frames_per_buffer=porcupine.frame_length
)

def get_next_audio_frame():
  pcm = audio_stream.read(porcupine.frame_length)
  pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)
  return pcm


print("Listening for wakeword...")

#running here
try:
  while True:
    audio_frame = get_next_audio_frame()
    keyword_index = porcupine.process(audio_frame)
    if keyword_index >= 0:
      test_stt()
      break

#when turned off
finally:
  audio_stream.close()
  paud.terminate()
  porcupine.delete()