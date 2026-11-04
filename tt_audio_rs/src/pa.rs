//! Minimal PortAudio bindings: just enough to open mono paInt16 streams the
//! way PyAudio does (default device, default low latency, paClipOff).
//!
//! This links the same libportaudio as PyAudio, so device handling (sample
//! rate conversion, channel mapping, buffer sizes) is identical between the
//! Python and Rust pipelines.

use std::ffi::{CStr, c_char, c_int, c_ulong, c_void};
use std::fmt;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::ptr;
use std::sync::Arc;
use std::sync::atomic::AtomicU64;
use std::sync::atomic::Ordering::Relaxed;
use std::time::Instant;

type PaError = c_int;
type PaDeviceIndex = c_int;
type PaStream = c_void;

#[repr(C)]
struct PaDeviceInfo {
    struct_version: c_int,
    name: *const c_char,
    host_api: c_int,
    max_input_channels: c_int,
    max_output_channels: c_int,
    default_low_input_latency: f64,
    default_low_output_latency: f64,
    default_high_input_latency: f64,
    default_high_output_latency: f64,
    default_sample_rate: f64,
}

#[repr(C)]
struct PaStreamParameters {
    device: PaDeviceIndex,
    channel_count: c_int,
    sample_format: c_ulong,
    suggested_latency: f64,
    host_api_specific_stream_info: *mut c_void,
}

type PaStreamCallback = unsafe extern "C" fn(
    input: *const c_void,
    output: *mut c_void,
    frame_count: c_ulong,
    time_info: *const c_void,
    status_flags: c_ulong,
    user_data: *mut c_void,
) -> c_int;

const PA_INT16: c_ulong = 0x8;
const PA_CLIP_OFF: c_ulong = 0x1;
const PA_CONTINUE: c_int = 0;
const PA_ABORT: c_int = 2;

/// Status bit: input data was discarded because a callback ran late.
const INPUT_OVERFLOW: c_ulong = 0x2;
/// Status bit: output had a gap because a callback ran late.
const OUTPUT_UNDERFLOW: c_ulong = 0x4;

/// Let PortAudio pick the host's buffer size (PyAudio's default).
pub const FRAMES_PER_BUFFER_UNSPECIFIED: u32 = 0;

unsafe extern "C" {
    fn Pa_Initialize() -> PaError;
    fn Pa_Terminate() -> PaError;
    fn Pa_GetErrorText(err: PaError) -> *const c_char;
    fn Pa_GetDefaultInputDevice() -> PaDeviceIndex;
    fn Pa_GetDefaultOutputDevice() -> PaDeviceIndex;
    fn Pa_GetDeviceInfo(device: PaDeviceIndex) -> *const PaDeviceInfo;
    fn Pa_OpenStream(
        stream: *mut *mut PaStream,
        input_parameters: *const PaStreamParameters,
        output_parameters: *const PaStreamParameters,
        sample_rate: f64,
        frames_per_buffer: c_ulong,
        stream_flags: c_ulong,
        callback: Option<PaStreamCallback>,
        user_data: *mut c_void,
    ) -> PaError;
    fn Pa_StartStream(stream: *mut PaStream) -> PaError;
    fn Pa_StopStream(stream: *mut PaStream) -> PaError;
    fn Pa_CloseStream(stream: *mut PaStream) -> PaError;
}

#[derive(Debug)]
pub struct Error(String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

fn check(err: PaError) -> Result<(), Error> {
    if err == 0 {
        return Ok(());
    }
    // SAFETY: Pa_GetErrorText returns a static, NUL-terminated string.
    let text = unsafe { CStr::from_ptr(Pa_GetErrorText(err)) };
    Err(Error(format!("PortAudio error {err}: {}", text.to_string_lossy())))
}

/// Holds one reference on PortAudio's (reference-counted) initialization,
/// so it can coexist with PyAudio in the same process.
struct Init;

impl Init {
    fn new() -> Result<Self, Error> {
        // SAFETY: plain C call; balanced by Pa_Terminate in Drop.
        check(unsafe { Pa_Initialize() })?;
        Ok(Init)
    }
}

impl Drop for Init {
    fn drop(&mut self) {
        // SAFETY: balances the Pa_Initialize in Init::new.
        unsafe { Pa_Terminate() };
    }
}

#[derive(Clone, Copy)]
pub enum Direction {
    Input,
    Output,
}

/// The device info borrows from `_init` because PortAudio frees it in Pa_Terminate.
fn default_device(_init: &Init, direction: Direction) -> Result<(PaDeviceIndex, &PaDeviceInfo), Error> {
    // SAFETY: PortAudio is initialized while `_init` is alive.
    unsafe {
        let device = match direction {
            Direction::Input => Pa_GetDefaultInputDevice(),
            Direction::Output => Pa_GetDefaultOutputDevice(),
        };
        let info = if device < 0 { ptr::null() } else { Pa_GetDeviceInfo(device) };
        match info.as_ref() {
            Some(info) => Ok((device, info)),
            None => Err(Error(match direction {
                Direction::Input => "no default input device".into(),
                Direction::Output => "no default output device".into(),
            })),
        }
    }
}

/// The default output device's sample rate, i.e. what PyAudioInterface reads
/// from `get_default_output_device_info()["defaultSampleRate"]`.
pub fn default_output_rate() -> Result<f64, Error> {
    let init = Init::new()?;
    Ok(default_device(&init, Direction::Output)?.1.default_sample_rate)
}

/// Processes one buffer of mono 16-bit audio on PortAudio's real-time thread.
/// For input streams `input` holds the captured audio and `output` is empty;
/// for output streams it's the other way around and `output` must be filled.
pub trait Callback: Send + 'static {
    fn process(&mut self, input: &[u8], output: &mut [u8]);
}

/// Per-stream counters kept by the real-time thread: how many callbacks ran,
/// how long they took, and how many arrived with PortAudio's xrun flag set
/// (input overflow / output underflow: audio was lost or had a gap).
#[derive(Default)]
pub struct CallbackStats {
    calls: AtomicU64,
    xruns: AtomicU64,
    busy_ns: AtomicU64,
    max_busy_ns: AtomicU64,
}

pub struct StatsSnapshot {
    pub calls: u64,
    pub xruns: u64,
    pub mean_us: f64,
    pub max_us: f64,
}

impl CallbackStats {
    fn record(&self, started: Instant, xrun: bool) {
        let ns = started.elapsed().as_nanos() as u64;
        self.calls.fetch_add(1, Relaxed);
        self.busy_ns.fetch_add(ns, Relaxed);
        self.max_busy_ns.fetch_max(ns, Relaxed);
        if xrun {
            self.xruns.fetch_add(1, Relaxed);
        }
    }

    pub fn snapshot(&self) -> StatsSnapshot {
        let calls = self.calls.load(Relaxed);
        StatsSnapshot {
            calls,
            xruns: self.xruns.load(Relaxed),
            mean_us: self.busy_ns.load(Relaxed) as f64 / calls.max(1) as f64 / 1e3,
            max_us: self.max_busy_ns.load(Relaxed) as f64 / 1e3,
        }
    }
}

/// What PortAudio's user_data points at.
struct Registered<C> {
    callback: C,
    stats: Arc<CallbackStats>,
    xrun_flag: c_ulong,
}

unsafe extern "C" fn trampoline<C: Callback>(
    input: *const c_void,
    output: *mut c_void,
    frame_count: c_ulong,
    _time_info: *const c_void,
    status: c_ulong,
    user_data: *mut c_void,
) -> c_int {
    let started = Instant::now();
    let len = frame_count as usize * 2;
    // SAFETY: PortAudio hands us frame_count frames of mono int16 in each
    // non-null buffer, plus the user_data pointer registered in Stream::open,
    // which stays valid until the stream is closed.
    let (input, output, registered) = unsafe {
        let input: &[u8] = if input.is_null() {
            &[]
        } else {
            std::slice::from_raw_parts(input.cast(), len)
        };
        let output: &mut [u8] = if output.is_null() {
            &mut []
        } else {
            std::slice::from_raw_parts_mut(output.cast(), len)
        };
        (input, output, &mut *user_data.cast::<Registered<C>>())
    };
    // A panic must not unwind into C. Stop the stream instead, the way PyAudio
    // aborts when a Python callback raises.
    match catch_unwind(AssertUnwindSafe(|| registered.callback.process(input, &mut *output))) {
        Ok(()) => {
            registered.stats.record(started, status & registered.xrun_flag != 0);
            PA_CONTINUE
        }
        Err(_) => {
            output.fill(0);
            PA_ABORT
        }
    }
}

/// A running mono paInt16 stream. Dropping it stops and closes the stream
/// (no callbacks run after that) and then frees the callback.
pub struct Stream {
    raw: *mut PaStream,
    user_data: *mut c_void,
    free_user_data: unsafe fn(*mut c_void),
    _init: Init,
}

// SAFETY: a PortAudio stream handle may be used from any thread, and Stream
// is only ever used by its owner.
unsafe impl Send for Stream {}

impl Stream {
    /// Opens and starts a stream on the default input or output device,
    /// configured exactly like PyAudio's `p.open(...)`.
    pub fn open<C: Callback>(
        direction: Direction,
        rate: u32,
        frames_per_buffer: u32,
        stats: Arc<CallbackStats>,
        callback: C,
    ) -> Result<Self, Error> {
        let init = Init::new()?;
        let (device, info) = default_device(&init, direction)?;
        let params = PaStreamParameters {
            device,
            channel_count: 1,
            sample_format: PA_INT16,
            suggested_latency: match direction {
                Direction::Input => info.default_low_input_latency,
                Direction::Output => info.default_low_output_latency,
            },
            host_api_specific_stream_info: ptr::null_mut(),
        };
        let (input, output, xrun_flag) = match direction {
            Direction::Input => (&raw const params, ptr::null(), INPUT_OVERFLOW),
            Direction::Output => (ptr::null(), &raw const params, OUTPUT_UNDERFLOW),
        };
        let registered = Registered { callback, stats, xrun_flag };
        let user_data = Box::into_raw(Box::new(registered)).cast::<c_void>();
        let mut raw = ptr::null_mut();
        // SAFETY: params outlive the call, and user_data stays valid until
        // Stream's Drop has closed the stream.
        let opened = check(unsafe {
            Pa_OpenStream(
                &mut raw,
                input,
                output,
                f64::from(rate),
                c_ulong::from(frames_per_buffer),
                PA_CLIP_OFF,
                Some(trampoline::<C>),
                user_data,
            )
        });
        if let Err(e) = opened {
            // SAFETY: the stream never opened, so PortAudio holds no pointer to it.
            unsafe { free_box::<Registered<C>>(user_data) };
            return Err(e);
        }
        let stream = Stream {
            raw,
            user_data,
            free_user_data: free_box::<Registered<C>>,
            _init: init,
        };
        // SAFETY: raw is an open stream; on failure Drop closes it.
        check(unsafe { Pa_StartStream(stream.raw) })?;
        Ok(stream)
    }
}

impl Drop for Stream {
    fn drop(&mut self) {
        // SAFETY: raw is open. Once Pa_CloseStream returns, PortAudio no
        // longer calls back, so the callback can be freed; `_init` is
        // released after this body runs.
        unsafe {
            Pa_StopStream(self.raw);
            Pa_CloseStream(self.raw);
            (self.free_user_data)(self.user_data);
        }
    }
}

/// # Safety
/// `ptr` must come from `Box::<C>::into_raw` and not be used afterwards.
unsafe fn free_box<C>(ptr: *mut c_void) {
    drop(unsafe { Box::from_raw(ptr.cast::<C>()) });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    struct Recorder(Arc<Mutex<Vec<usize>>>);

    impl Callback for Recorder {
        fn process(&mut self, input: &[u8], output: &mut [u8]) {
            output.fill(0); // silence
            self.0.lock().unwrap().push(input.len().max(output.len()));
        }
    }

    // Needs real audio hardware: `cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn streams_deliver_fixed_size_buffers() {
        assert!(default_output_rate().unwrap() > 0.0);
        for direction in [Direction::Output, Direction::Input] {
            let sizes = Arc::new(Mutex::new(Vec::new()));
            let stats = Arc::new(CallbackStats::default());
            let recorder = Recorder(Arc::clone(&sizes));
            let stream = Stream::open(direction, 24000, 1024, Arc::clone(&stats), recorder).unwrap();
            std::thread::sleep(Duration::from_millis(300));
            drop(stream);
            let sizes = sizes.lock().unwrap();
            assert!(sizes.len() >= 3, "only {} callbacks", sizes.len());
            assert!(sizes.iter().all(|&n| n == 1024 * 2));
            assert_eq!(stats.snapshot().calls, sizes.len() as u64);
        }
    }
}
