//! Rust port of `tt/utils/audio_interface_openai.py`: the mic/speaker layer
//! of the OpenAI Realtime backend.

use std::sync::atomic::Ordering::Relaxed;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Arc, Mutex};

use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use crate::dsp;
use crate::pa::{self, CallbackStats, Direction, Stream};
use crate::ring::Ring;

/// -5 dB, applied to the mic while TTS is playing (same factor as Python).
const DUCK_FACTOR: f64 = 0.5623;
/// `pyaudio.paInt16`, the only sample format the pipeline uses.
pub const PA_INT16: u32 = 8;
pub const PA_CONTINUE: i32 = 0;
/// The Python buffers are unbounded; these rings hold this much audio.
pub const TTS_BUFFER_SECONDS: usize = 300;
pub const MIC_BUFFER_SECONDS: usize = 60;

struct Shared {
    /// Input callback -> `read_mic_chunk`.
    mic: Ring,
    /// `push_tts` -> output callback.
    tts: Ring,
    ducking: AtomicBool,
    mic_dropped: AtomicU64,
    tts_dropped: AtomicU64,
}

impl Shared {
    /// `_mic_cb`: duck the mic while TTS is playing, then queue the audio.
    fn capture(&self, input: &[u8], scratch: &mut Vec<u8>) {
        let data = if self.ducking.load(Relaxed) {
            scratch.clear();
            scratch.extend_from_slice(input);
            dsp::mul(scratch, DUCK_FACTOR);
            &scratch[..]
        } else {
            input
        };
        let dropped = data.len() - self.mic.push(data);
        self.mic_dropped.fetch_add(dropped as u64, Relaxed);
    }

    /// `_spk_cb`: play buffered TTS, padding with silence when there isn't a
    /// full buffer's worth. Ducking stays on only while TTS keeps up.
    fn render(&self, output: &mut [u8]) {
        let n = self.tts.pop(output);
        output[n..].fill(0);
        self.ducking.store(n == output.len(), Relaxed);
    }
}

struct Mic {
    shared: Arc<Shared>,
    scratch: Vec<u8>,
}

impl pa::Callback for Mic {
    fn process(&mut self, input: &[u8], _output: &mut [u8]) {
        self.shared.capture(input, &mut self.scratch);
    }
}

struct Speaker(Arc<Shared>);

impl pa::Callback for Speaker {
    fn process(&mut self, _input: &[u8], output: &mut [u8]) {
        self.0.render(output);
    }
}

pub fn stats_dict<'py>(py: Python<'py>, stats: &CallbackStats) -> PyResult<Bound<'py, PyDict>> {
    let s = stats.snapshot();
    let d = PyDict::new(py);
    d.set_item("callbacks", s.calls)?;
    d.set_item("xruns", s.xruns)?;
    d.set_item("mean_us", s.mean_us)?;
    d.set_item("max_us", s.max_us)?;
    Ok(d)
}

/// Drop-in replacement for `tt.utils.audio_interface_openai.AudioIO`.
///
/// The PortAudio callbacks run entirely in Rust and never touch the GIL;
/// Python only interacts through `push_tts` and `read_mic_chunk`, which
/// exchange audio with the callbacks through lock-free rings.
#[pyclass(frozen)]
pub struct AudioIO {
    #[pyo3(get)]
    chunk_size: usize,
    #[pyo3(get)]
    rate: u32,
    shared: Arc<Shared>,
    mic_stats: Arc<CallbackStats>,
    speaker_stats: Arc<CallbackStats>,
    /// Only locked with the GIL released, or by code that can't block on it.
    streams: Mutex<Option<(Stream, Stream)>>,
}

impl AudioIO {
    /// The `_mic_cb`/`_spk_cb` hooks stand in for the real-time callbacks, so
    /// they may only run while the streams are closed (otherwise each ring
    /// would get a second producer or consumer).
    fn while_stopped<T>(&self, f: impl FnOnce() -> T) -> PyResult<T> {
        let streams = self.streams.lock().unwrap();
        if streams.is_some() {
            return Err(PyRuntimeError::new_err(
                "call stop() before driving the callbacks by hand",
            ));
        }
        Ok(f())
    }
}

#[pymethods]
impl AudioIO {
    #[new]
    #[pyo3(signature = (chunk_size=1024, rate=24000, format=PA_INT16))]
    fn new(chunk_size: usize, rate: u32, format: u32) -> PyResult<Self> {
        if format != PA_INT16 {
            return Err(PyValueError::new_err("only pyaudio.paInt16 is supported"));
        }
        let bytes_per_second = rate as usize * 2;
        Ok(Self {
            chunk_size,
            rate,
            shared: Arc::new(Shared {
                mic: Ring::new(bytes_per_second * MIC_BUFFER_SECONDS),
                tts: Ring::new(bytes_per_second * TTS_BUFFER_SECONDS),
                ducking: AtomicBool::new(false),
                mic_dropped: AtomicU64::new(0),
                tts_dropped: AtomicU64::new(0),
            }),
            mic_stats: Arc::default(),
            speaker_stats: Arc::default(),
            streams: Mutex::new(None),
        })
    }

    /// Open the mic and speaker: mono paInt16 at `rate`, `chunk_size` frames
    /// per callback, like the Python version.
    fn start(&self, py: Python<'_>) -> PyResult<()> {
        let frames = self.chunk_size as u32;
        py.detach(|| {
            let mut streams = self.streams.lock().unwrap();
            if streams.is_none() {
                let mic = Mic {
                    shared: Arc::clone(&self.shared),
                    scratch: Vec::with_capacity(self.chunk_size * 2),
                };
                let speaker = Speaker(Arc::clone(&self.shared));
                let mic = Stream::open(Direction::Input, self.rate, frames, Arc::clone(&self.mic_stats), mic)?;
                let speaker = Stream::open(
                    Direction::Output,
                    self.rate,
                    frames,
                    Arc::clone(&self.speaker_stats),
                    speaker,
                )?;
                *streams = Some((mic, speaker));
            }
            Ok(())
        })
        .map_err(|e: pa::Error| PyOSError::new_err(e.to_string()))
    }

    fn stop(&self, py: Python<'_>) {
        py.detach(|| drop(self.streams.lock().unwrap().take()));
    }

    fn push_tts(&self, audio_bytes: &[u8]) {
        let dropped = audio_bytes.len() - self.shared.tts.push(audio_bytes);
        if dropped > 0 {
            self.shared.tts_dropped.fetch_add(dropped as u64, Relaxed);
            eprintln!("tt_audio_rs: TTS buffer full, dropped {dropped} bytes");
        }
    }

    /// One `chunk_size`-frame chunk of mic audio, or None if none is ready.
    fn read_mic_chunk<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyBytes>>> {
        let len = self.chunk_size * 2;
        if self.shared.mic.len() < len {
            return Ok(None);
        }
        PyBytes::new_with(py, len, |buf| {
            self.shared.mic.pop(buf);
            Ok(())
        })
        .map(Some)
    }

    #[getter]
    fn ducking(&self) -> bool {
        self.shared.ducking.load(Relaxed)
    }

    #[setter]
    fn set_ducking(&self, value: bool) {
        self.shared.ducking.store(value, Relaxed);
    }

    /// Callback timings and xrun counts per stream, plus bytes dropped
    /// because a ring was full.
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("mic", stats_dict(py, &self.mic_stats)?)?;
        d.set_item("speaker", stats_dict(py, &self.speaker_stats)?)?;
        d.set_item("mic_dropped_bytes", self.shared.mic_dropped.load(Relaxed))?;
        d.set_item("tts_dropped_bytes", self.shared.tts_dropped.load(Relaxed))?;
        Ok(d)
    }

    /// Same contract as the Python `_mic_cb`; runs the input-callback logic
    /// on the calling thread. Only allowed while stopped.
    fn _mic_cb(
        &self,
        in_data: &[u8],
        _frame_count: usize,
        _time_info: &Bound<'_, PyAny>,
        _status: &Bound<'_, PyAny>,
    ) -> PyResult<(Option<()>, i32)> {
        self.while_stopped(|| self.shared.capture(in_data, &mut Vec::with_capacity(in_data.len())))?;
        Ok((None, PA_CONTINUE))
    }

    /// Same contract as the Python `_spk_cb`; runs the output-callback logic
    /// on the calling thread. Only allowed while stopped.
    fn _spk_cb<'py>(
        &self,
        py: Python<'py>,
        _in_data: &Bound<'py, PyAny>,
        frame_count: usize,
        _time_info: &Bound<'py, PyAny>,
        _status: &Bound<'py, PyAny>,
    ) -> PyResult<(Bound<'py, PyBytes>, i32)> {
        let chunk = self.while_stopped(|| {
            PyBytes::new_with(py, frame_count * 2, |buf| {
                self.shared.render(buf);
                Ok(())
            })
        })??;
        Ok((chunk, PA_CONTINUE))
    }
}
