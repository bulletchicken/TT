# Python vs Rust audio pipeline

Apple M3 / macOS 26.6.2 / Python 3.13.1 / PortAudio V19.7.0-devel, revision 147dd722548358763a8b649b3e4b41dfffbcfbb6

## DSP kernels (per call, from Python)

| operation | Python audioop (us) | Rust (us) | speedup |
|---|---|---|---|
| mul, 1024-frame mic chunk (OpenAI ducking) | 0.95 | 0.76 | 1.3x |
| mul, 4000-frame mic chunk (ElevenLabs ducking) | 3.57 | 2.71 | 1.3x |
| ratecv 16k->48k, 100 ms chunk | 14.33 | 4.32 | 3.3x |
| ratecv 16k->48k, 500 ms chunk | 69.34 | 21.19 | 3.3x |

## Pipeline operations (per call, from Python)

| operation | Python (us) | Rust (us) | speedup |
|---|---|---|---|
| speaker callback + push_tts, 0.1 s of TTS buffered | 0.47 | 0.21 | 2.2x |
| speaker callback + push_tts, 1 s of TTS buffered | 1.68 | 0.22 | 7.6x |
| speaker callback + push_tts, 10 s of TTS buffered | 6.82 | 0.21 | 32.0x |
| speaker callback + push_tts, 60 s of TTS buffered | 53.44 | 0.21 | 253.6x |
| mic callback (ducked) + read_mic_chunk | 1.28 | 0.95 | 1.4x |
| ElevenLabs output() of 100 ms + dequeue | 14.59 | 4.51 | 3.2x |

## push_tts racing the speaker callback (3 s, thread switch every 1 us)

| implementation | TTS bytes pushed | bytes played | bytes lost |
|---|---|---|---|
| Python | 22,061,760 | 22,056,000 | 5,760 |
| Rust | 1,168,204,800 | 1,168,204,800 | 0 |

## Real devices, 10 s per run

### OpenAI AudioIO (24 kHz, 1024-frame callbacks)

| load | impl | stream | callbacks (got / expected) | GIL wait p50 / p99 / max (ms) | callback done by, max (ms) | note |
|---|---|---|---|---|---|---|
| idle | Python | speaker | 238 / 235 | 0.07 / 0.10 / 0.13 | 0.15 |  |
| idle | Python | mic | 235 / 235 | 0.08 / 0.11 / 0.13 | 0.16 |  |
| idle | Rust | speaker | 238 / 235 | 0.00 / 0.00 / 0.00 | 0.00 |  |
| idle | Rust | mic | 234 / 235 | 0.00 / 0.00 / 0.00 | 0.02 |  |
| busy (1 CPU-bound thread) | Python | speaker | 237 / 235 | 5.11 / 16.46 / 24.26 | 24.26 |  |
| busy (1 CPU-bound thread) | Python | mic | 226 / 235 | 5.06 / 15.15 / 25.20 | 25.20 |  |
| busy (1 CPU-bound thread) | Rust | speaker | 238 / 235 | 0.00 / 0.00 / 0.00 | 0.00 |  |
| busy (1 CPU-bound thread) | Rust | mic | 235 / 235 | 0.00 / 0.00 / 0.00 | 0.01 |  |
| heavy (3 CPU threads + 24 ms GIL holds) | Python | speaker | 153 / 242 | 18.34 / 121.68 / 137.07 | 137.09 |  |
| heavy (3 CPU threads + 24 ms GIL holds) | Python | mic | 143 / 242 | 27.02 / 149.66 / 246.72 | 246.73 |  |
| heavy (3 CPU threads + 24 ms GIL holds) | Rust | speaker | 242 / 239 | 0.00 / 0.00 / 0.00 | 0.00 |  |
| heavy (3 CPU threads + 24 ms GIL holds) | Rust | mic | 238 / 239 | 0.00 / 0.00 / 0.00 | 0.01 |  |

### ElevenLabs interface (16 kHz mic, 4000-frame callbacks)

| load | impl | stream | callbacks (got / expected) | GIL wait p50 / p99 / max (ms) | callback done by, max (ms) | note |
|---|---|---|---|---|---|---|
| idle | Python | mic | 39 / 40 | 0.12 / 0.19 / 0.20 | 0.76 | 39 chunks sent to input_callback |
| idle | Rust | mic | 39 / 40 | 0.00 / 0.00 / 0.00 | 0.08 | 39 chunks sent to input_callback |
| idle | Rust | speaker | 7591 | 0.00 / 0.00 / 0.00 | 0.00 |  |
| busy (1 CPU-bound thread) | Python | mic | 39 / 40 | 5.13 / 8.30 / 10.21 | 48.19 | 39 chunks sent to input_callback |
| busy (1 CPU-bound thread) | Rust | mic | 40 / 40 | 0.00 / 0.00 / 0.00 | 0.05 | 40 chunks sent to input_callback |
| busy (1 CPU-bound thread) | Rust | speaker | 7663 | 0.00 / 0.00 / 0.00 | 0.00 |  |
| heavy (3 CPU threads + 24 ms GIL holds) | Python | mic | 27 / 41 | 20.12 / 92.70 / 110.29 | 229.43 | 27 chunks sent to input_callback |
| heavy (3 CPU threads + 24 ms GIL holds) | Rust | mic | 40 / 41 | 0.00 / 0.00 / 0.00 | 0.03 | 40 chunks sent to input_callback |
| heavy (3 CPU threads + 24 ms GIL holds) | Rust | speaker | 7788 | 0.00 / 0.00 / 0.00 | 0.00 |  |

