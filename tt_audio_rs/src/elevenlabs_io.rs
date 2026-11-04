//! Rust port of `tt/utils/audio_interface.py` (`PyAudioInterface`): the
//! mic/speaker layer of the ElevenLabs backend. `RustAudioInterface` in
//! `tt/utils/audio_interface_rust.py` wraps it in ElevenLabs' AudioInterface.

use std::sync::atomic::Ordering::{Acquire, Relaxed, Release};
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::{self, JoinHandle, Thread};
use std::time::Duration;

use pyo3::exceptions::{PyOSError, PyRuntimeError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use crate::dsp::{self, RateCv};
use crate::openai_io::{MIC_BUFFER_SECONDS, TTS_BUFFER_SECONDS};
use crate::pa::{self, CallbackStats, Direction, FRAMES_PER_BUFFER_UNSPECIFIED, Stream};
use crate::ring::Ring;
use crate::{check_whole_frames, stats_dict};

/// ElevenLabs sends and expects 16 kHz 16-bit mono PCM.
const ELEVENLABS_RATE: u32 = 16000;
/// 250 ms mic chunks, as in Python.
const INPUT_FRAMES: usize = 4000;
/// -20 dB, applied to the mic while the agent is speaking.
const DUCK_FACTOR: f64 = 0.1;
/// Python's play loop drops ducking after 0.2 s without new audio.
const DUCK_HOLD_SECONDS: f64 = 0.2;

struct Shared {
    /// Input callback -> delivery thread.
    mic: Ring,
    /// `output` -> output callback, already at the hardware rate.
    out: Ring,
    ducking: AtomicBool,
    stopping: AtomicBool,
    /// The delivery thread, which the input callback wakes after each chunk.
    deliverer: OnceLock<Thread>,
    mic_dropped: AtomicU64,
    out_dropped: AtomicU64,
}

struct Mic {
    shared: Arc<Shared>,
    scratch: Vec<u8>,
}

impl pa::Callback for Mic {
    fn process(&mut self, input: &[u8], _output: &mut [u8]) {
        let data = if self.shared.ducking.load(Relaxed) {
            self.scratch.clear();
            self.scratch.extend_from_slice(input);
            dsp::mul(&mut self.scratch, DUCK_FACTOR);
            &self.scratch[..]
        } else {
            input
        };
        let dropped = data.len() - self.shared.mic.push(data);
        self.shared.mic_dropped.fetch_add(dropped as u64, Relaxed);
        if let Some(deliverer) = self.shared.deliverer.get() {
            deliverer.unpark();
        }
    }
}

struct Speaker {
    shared: Arc<Shared>,
    silent_frames: usize,
    hold_frames: usize,
}

impl pa::Callback for Speaker {
    fn process(&mut self, _input: &[u8], output: &mut [u8]) {
        let n = self.shared.out.pop(output);
        output[n..].fill(0);
        if n > 0 {
            self.silent_frames = 0;
            self.shared.ducking.store(true, Relaxed);
        } else {
            self.silent_frames += output.len() / 2;
            if self.silent_frames >= self.hold_frames {
                self.shared.ducking.store(false, Relaxed);
            }
        }
    }
}

/// Hands each 250 ms mic chunk to the Python `input_callback`. This is the
/// only thread that takes the GIL; in Python the PortAudio thread itself does.
fn deliver(shared: &Shared, input_callback: Py<PyAny>) {
    let mut chunk = vec![0u8; INPUT_FRAMES * 2];
    while !shared.stopping.load(Acquire) {
        if shared.mic.len() < chunk.len() {
            thread::park_timeout(Duration::from_millis(100));
            continue;
        }
        shared.mic.pop(&mut chunk);
        let keep_going = Python::try_attach(|py| {
            // stop() may have run while this thread waited for the GIL.
            if shared.stopping.load(Acquire) {
                return false;
            }
            match input_callback.call1(py, (PyBytes::new(py, &chunk),)) {
                Ok(_) => true,
                // PyAudio aborts the stream when the callback raises.
                Err(e) => {
                    e.print(py);
                    false
                }
            }
        });
        // None: the interpreter is shutting down.
        if keep_going != Some(true) {
            break;
        }
    }
}

enum State {
    Idle,
    Running {
        mic: Stream,
        speaker: Stream,
        deliverer: JoinHandle<()>,
    },
    Stopped,
}

/// The engine behind `RustAudioInterface`, a drop-in replacement for
/// `tt.utils.audio_interface.PyAudioInterface`.
///
/// Like the ElevenLabs SDK's own usage, an instance is started once and
/// stopped once.
#[pyclass(frozen, module = "tt_audio_rs")]
pub struct ElevenLabsAudio {
    #[pyo3(get)]
    hw_rate: u32,
    shared: Arc<Shared>,
    /// 16 kHz -> hardware rate; None when the device already runs at 16 kHz.
    resampler: Mutex<Option<RateCv>>,
    mic_stats: Arc<CallbackStats>,
    speaker_stats: Arc<CallbackStats>,
    /// Only held briefly and never while waiting on the GIL or a thread.
    state: Mutex<State>,
}

#[pymethods]
impl ElevenLabsAudio {
    #[new]
    fn new(py: Python<'_>) -> PyResult<Self> {
        let hw_rate = py
            .detach(pa::default_output_rate)
            .map_err(|e| PyOSError::new_err(e.to_string()))? as u32;
        Ok(Self {
            hw_rate,
            shared: Arc::new(Shared {
                mic: Ring::new(ELEVENLABS_RATE as usize * 2 * MIC_BUFFER_SECONDS),
                out: Ring::new(hw_rate as usize * 2 * TTS_BUFFER_SECONDS),
                ducking: AtomicBool::new(false),
                stopping: AtomicBool::new(false),
                deliverer: OnceLock::new(),
                mic_dropped: AtomicU64::new(0),
                out_dropped: AtomicU64::new(0),
            }),
            resampler: Mutex::new((hw_rate != ELEVENLABS_RATE).then(|| RateCv::new(ELEVENLABS_RATE, hw_rate))),
            mic_stats: Arc::default(),
            speaker_stats: Arc::default(),
            state: Mutex::new(State::Idle),
        })
    }

    /// Open the mic (16 kHz, 4000-frame callbacks) and the speaker (hardware
    /// rate), then start delivering mic chunks to `input_callback`.
    fn start(&self, py: Python<'_>, input_callback: Py<PyAny>) -> PyResult<()> {
        let os_error = |e: &dyn std::fmt::Display| PyOSError::new_err(e.to_string());
        py.detach(|| {
            let mut state = self.state.lock().unwrap();
            match *state {
                State::Running { .. } => return Ok(()),
                State::Stopped => {
                    return Err(PyRuntimeError::new_err(
                        "this audio interface was stopped; create a new one",
                    ));
                }
                State::Idle => {}
            }
            let mic = Mic {
                shared: Arc::clone(&self.shared),
                scratch: Vec::with_capacity(INPUT_FRAMES * 2),
            };
            let speaker = Speaker {
                shared: Arc::clone(&self.shared),
                silent_frames: 0,
                hold_frames: (f64::from(self.hw_rate) * DUCK_HOLD_SECONDS) as usize,
            };
            let mic = Stream::open(
                Direction::Input,
                ELEVENLABS_RATE,
                INPUT_FRAMES as u32,
                Arc::clone(&self.mic_stats),
                mic,
            )
            .map_err(|e| os_error(&e))?;
            let speaker = Stream::open(
                Direction::Output,
                self.hw_rate,
                FRAMES_PER_BUFFER_UNSPECIFIED,
                Arc::clone(&self.speaker_stats),
                speaker,
            )
            .map_err(|e| os_error(&e))?;
            let shared = Arc::clone(&self.shared);
            let deliverer = thread::Builder::new()
                .name("tt-audio-mic".into())
                .spawn(move || deliver(&shared, input_callback))
                .map_err(|e| os_error(&e))?;
            let _ = self.shared.deliverer.set(deliverer.thread().clone());
            *state = State::Running { mic, speaker, deliverer };
            Ok(())
        })
    }

    /// Close both streams. Once this returns, `input_callback` won't be called
    /// again (unless stop() was called from inside it, in which case that
    /// call is the last one).
    fn stop(&self, py: Python<'_>) {
        py.detach(|| {
            let previous = std::mem::replace(&mut *self.state.lock().unwrap(), State::Stopped);
            let State::Running { mic, speaker, deliverer } = previous else {
                return;
            };
            self.shared.stopping.store(true, Release);
            drop(mic);
            drop(speaker);
            deliverer.thread().unpark();
            // The ElevenLabs SDK calls stop() from inside input_callback when
            // the socket closes; that thread exits on its own once it returns.
            if deliverer.thread().id() != thread::current().id() {
                let _ = deliverer.join();
            }
        });
    }

    /// Queue 16 kHz agent audio for playback (resampled to the hardware rate
    /// with the same stateful ratecv as Python).
    fn output(&self, audio: &[u8]) -> PyResult<()> {
        check_whole_frames(audio)?;
        let mut resampler = self.resampler.lock().unwrap();
        let dropped = match resampler.as_mut() {
            Some(rc) => {
                let mut resampled = Vec::new();
                rc.process(audio, &mut resampled);
                resampled.len() - self.shared.out.push(&resampled)
            }
            None => audio.len() - self.shared.out.push(audio),
        };
        if dropped > 0 {
            self.shared.out_dropped.fetch_add(dropped as u64, Relaxed);
            eprintln!("tt_audio_rs: output buffer full, dropped {dropped} bytes");
        }
        Ok(())
    }

    /// Drop all buffered agent audio (the user barged in).
    fn interrupt(&self) {
        let mut resampler = self.resampler.lock().unwrap();
        self.shared.out.clear();
        if let Some(rc) = resampler.as_mut() {
            rc.reset();
        }
    }

    #[getter]
    fn ducking(&self) -> bool {
        self.shared.ducking.load(Relaxed)
    }

    /// Callback timings and xrun counts per stream, plus bytes dropped
    /// because a ring was full.
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("mic", stats_dict(py, &self.mic_stats)?)?;
        d.set_item("speaker", stats_dict(py, &self.speaker_stats)?)?;
        d.set_item("mic_dropped_bytes", self.shared.mic_dropped.load(Relaxed))?;
        d.set_item("out_dropped_bytes", self.shared.out_dropped.load(Relaxed))?;
        Ok(d)
    }

    /// Everything queued for playback, consumed the way the speaker callback
    /// would. For tests; only allowed while not running.
    fn _drain<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let state = self.state.lock().unwrap();
        if matches!(*state, State::Running { .. }) {
            return Err(PyRuntimeError::new_err("call stop() before draining by hand"));
        }
        PyBytes::new_with(py, self.shared.out.len(), |buf| {
            self.shared.out.pop(buf);
            Ok(())
        })
    }
}

impl Drop for ElevenLabsAudio {
    fn drop(&mut self) {
        // Garbage-collected without stop(): let the delivery thread exit.
        self.shared.stopping.store(true, Release);
        if let Some(deliverer) = self.shared.deliverer.get() {
            deliverer.unpark();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pa::Callback;

    #[test]
    fn ducking_holds_for_200ms_after_playback() {
        let shared = Arc::new(Shared {
            mic: Ring::new(16),
            out: Ring::new(4096),
            ducking: AtomicBool::new(false),
            stopping: AtomicBool::new(false),
            deliverer: OnceLock::new(),
            mic_dropped: AtomicU64::new(0),
            out_dropped: AtomicU64::new(0),
        });
        let mut speaker = Speaker {
            shared: Arc::clone(&shared),
            silent_frames: 0,
            hold_frames: 9600, // 0.2 s at 48 kHz
        };
        let mut buf = vec![0u8; 1024]; // 512 frames
        shared.out.push(&[1; 960]);
        speaker.process(&[], &mut buf);
        assert!(shared.ducking.load(Relaxed));
        assert_eq!(buf[959..961], [1, 0]);
        for _ in 0..18 {
            speaker.process(&[], &mut buf); // 9216 silent frames: still holding
        }
        assert!(shared.ducking.load(Relaxed));
        speaker.process(&[], &mut buf); // 9728 >= 9600
        assert!(!shared.ducking.load(Relaxed));
    }
}
