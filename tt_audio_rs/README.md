# tt_audio_rs

A Rust copy of TED's audio pipeline (mic capture, ducking, resampling, TTS
playback) for both conversation backends. The Python pipeline in
`tt/utils/audio_interface.py` and `tt/utils/audio_interface_openai.py` is
unchanged and stays the default; the Rust one is opt-in.

## Using it

```bash
brew install portaudio        # PyAudio needs it already
pip install ./tt_audio_rs     # needs a Rust toolchain (cargo)

python -m tt.main --audio rust            # or
TT_AUDIO_BACKEND=rust python -m tt.main
```

Leaving `TT_AUDIO_BACKEND` unset (or `python`) keeps the Python pipeline.

| Python (default) | Rust | Backend |
|---|---|---|
| `tt.utils.audio_interface_openai.AudioIO` | `tt_audio_rs.AudioIO` | OpenAI |
| `tt.utils.audio_interface.PyAudioInterface` | `tt.utils.audio_interface_rust.RustAudioInterface` (wraps `tt_audio_rs.ElevenLabsAudio`) | ElevenLabs |

For development, `maturin develop --release` inside `tt_audio_rs/` rebuilds
and installs it into the active virtualenv.

If linking fails with `unknown architecture 'arm64e.x1'`, the Command Line
Tools ship a macOS SDK newer than their linker can read. Build against the
older SDK instead:
`SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk pip install ./tt_audio_rs`.

## What changes

The audio is the same. Ducking (`audioop.mul`) and resampling
(`audioop.ratecv`) are bit-exact ports, and streams are opened through the
same libportaudio with the same settings PyAudio uses. What changes is where
the work runs:

- **The real-time audio thread never waits for Python.** PyAudio runs every
  callback on CoreAudio's I/O thread after taking the GIL, so audio waits
  whenever another Python thread is busy (parsing websocket traffic, running a
  tool, saving memories). The Rust callbacks are plain Rust and swap audio with
  Python through lock-free ring buffers.
- **ElevenLabs mic chunks are sent from their own thread.** In Python, the
  SDK's `input_callback` (base64 + JSON + websocket send) runs inside the audio
  callback. Here a delivery thread makes that call.
- **TTS buffering is O(1).** The Python speaker callback re-copies the whole
  unplayed bytearray every 43 ms (`self.audio_buffer = self.audio_buffer[needed:]`).
  `_spk_cb` also reads and replaces the buffer in several steps, so a
  `push_tts` landing in between can be dropped. The Rust ring has neither
  problem.
- **`stop()` works.** `PyAudioInterface.stop()` raises `AttributeError`
  (`self.should_stop` doesn't exist), so its streams keep running after a
  session ends.
- **`interrupt()` is immediate.** Python still finishes the chunk it already
  handed to PortAudio. Rust drops everything buffered within one callback.

Smaller differences: the rings hold 300 s of TTS and 60 s of mic audio
(Python's buffers are unbounded; a full ring drops audio and counts it in
`stats()`). `ElevenLabsAudio` is single-use, which is how the SDK uses it.
`start()`/`stop()` are idempotent, and `stats()` reports per-stream callback
timings.

## Results

From `python tt_audio_rs/bench/compare.py` on an Apple M3, macOS 26.6,
Python 3.13.1, PortAudio 19.7. The full report is in
[bench/results.md](bench/results.md).

### Real devices while other Python threads are busy

Each run lasted 10 s and played silence. "Expected" is how many callbacks the
elapsed time requires. A missing callback means CoreAudio skipped that I/O
cycle while the callback was blocked, which is a gap in playback or mic audio
that never arrives. In a shorter 3 s run of the same scenarios, PortAudio's
xrun flags stayed at 0 even while callbacks were being skipped, so on macOS
they can't detect this.

"Heavy" is three CPU-bound threads plus a 24 ms GIL-holding `json.loads`
four times a second.

**OpenAI `AudioIO`** (24 kHz, 1024-frame callbacks)

| load | impl | speaker callbacks (got / expected) | mic callbacks (got / expected) | longest wait before the callback ran |
|---|---|---|---|---|
| idle | Python | 238 / 235 | 235 / 235 | 0.13 ms |
| idle | Rust | 238 / 235 | 234 / 235 | none |
| 1 busy thread | Python | 237 / 235 | 226 / 235 | 25 ms |
| 1 busy thread | Rust | 238 / 235 | 235 / 235 | none |
| heavy | Python | 153 / 242 | 143 / 242 | 247 ms |
| heavy | Rust | 242 / 239 | 238 / 239 | none |

**ElevenLabs interface** (16 kHz mic, 4000-frame callbacks)

| load | impl | mic callbacks (got / expected) | longest wait | slowest callback |
|---|---|---|---|---|
| idle | Python | 39 / 40 | 0.20 ms | 0.76 ms |
| idle | Rust | 39 / 40 | none | 0.08 ms |
| 1 busy thread | Python | 39 / 40 | 10 ms | 48 ms |
| 1 busy thread | Rust | 40 / 40 | none | 0.05 ms |
| heavy | Python | 27 / 41 | 110 ms | 229 ms |
| heavy | Rust | 40 / 41 | none | 0.03 ms |

Python's mic callback also does the SDK's `input_callback` work (base64,
JSON, socket send). Rust does that on its delivery thread, which delivered
every captured chunk.

At idle the two are equivalent. With a single busy Python thread, Python's
callbacks routinely wait about 5 ms (the GIL switch interval), and about 4% of
OpenAI mic chunks go missing. Under heavy load, Python loses 34–41% of the
audio in both directions. Rust callbacks finished within 0.1 ms in every run.

### Cost per operation

Measured in µs per call from Python. The Rust numbers include PyO3 call
overhead, which the real callbacks don't pay because they never go through
Python.

| operation | Python | Rust | speedup |
|---|---|---|---|
| speaker callback + push_tts, 0.1 s of TTS buffered | 0.47 | 0.21 | 2.2x |
| speaker callback + push_tts, 1 s buffered | 1.68 | 0.22 | 7.6x |
| speaker callback + push_tts, 10 s buffered | 6.82 | 0.21 | 32x |
| speaker callback + push_tts, 60 s buffered | 53.44 | 0.21 | 254x |
| mic callback (ducked) + read_mic_chunk | 1.28 | 0.95 | 1.4x |
| ElevenLabs `output()` of 100 ms + dequeue | 14.59 | 4.51 | 3.2x |
| `mul`, 1024-frame chunk (audioop is C) | 0.95 | 0.76 | 1.3x |
| `ratecv` 16k->48k, 100 ms chunk (audioop is C) | 14.33 | 4.32 | 3.3x |

How much TTS sits in the buffer depends on how fast the model's audio
arrives. When it streams faster than real time, Python's per-callback cost
grows with the backlog.

### push_tts racing the speaker callback

The test ran for 3 s with forced thread switches every 1 µs. Python lost
5,760 of 22 M bytes, and Rust lost none of 1.17 G. At normal switch intervals
the Python loss is rarer, but it is a correctness bug, not a speed issue.

## Tests

```bash
cd tt_audio_rs && cargo test          # kernels, ring buffer, ducking hold
cargo test -- --ignored               # + opens real PortAudio streams
cd .. && python -m unittest discover -s tt_audio_rs/tests                 # Python vs Rust, byte for byte
TT_AUDIO_HW=1 python -m unittest discover -s tt_audio_rs/tests            # + real devices (silence only)
```

The Python suite runs both implementations through identical inputs:
- `mul` on all 65,536 samples
- `ratecv` across 8 rate pairs with chunked state
- 5,000 random speaker/mic/TTS operations through the PyAudio-style callbacks
- ElevenLabs playback with interruptions

It asserts byte-identical results.

## Layout

- `src/dsp.rs`: `mul` and `ratecv` ports
- `src/ring.rs`: lock-free single-producer/single-consumer ring
- `src/pa.rs`: PortAudio bindings and per-callback stats
- `src/openai_io.rs`: `AudioIO`
- `src/elevenlabs_io.rs`: `ElevenLabsAudio`
- `tests/`, `bench/`: equivalence tests and the comparison harness

Not ported: wake-word detection (`tt/ears/wakeword_start.py`, Porcupine) and
everything on the network side.
