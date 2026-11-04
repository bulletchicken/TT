//! Rust port of TT's audio pipeline (`tt/utils/audio_interface*.py`),
//! exposed to Python through PyO3. See README.md.

mod dsp;
mod ring;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

type RatecvState = (i32, ((i32, i32),));

fn check_16bit_mono(width: u32, nchannels: u32) -> PyResult<()> {
    if width != 2 || nchannels != 1 {
        return Err(PyValueError::new_err("only 16-bit mono audio is supported"));
    }
    Ok(())
}

fn check_whole_frames(fragment: &[u8]) -> PyResult<()> {
    if !fragment.len().is_multiple_of(2) {
        return Err(PyValueError::new_err("not a whole number of frames"));
    }
    Ok(())
}

/// Drop-in for `audioop.mul(fragment, width, factor)` with 16-bit samples.
#[pyfunction]
fn mul<'py>(
    py: Python<'py>,
    fragment: &[u8],
    width: u32,
    factor: f64,
) -> PyResult<Bound<'py, PyBytes>> {
    check_16bit_mono(width, 1)?;
    check_whole_frames(fragment)?;
    PyBytes::new_with(py, fragment.len(), |buf| {
        buf.copy_from_slice(fragment);
        dsp::mul(buf, factor);
        Ok(())
    })
}

/// Drop-in for `audioop.ratecv(fragment, width, nchannels, inrate, outrate,
/// state)` with 16-bit mono samples and the default weights. Returns
/// `(bytes, state)` in audioop's state layout.
#[pyfunction]
fn ratecv<'py>(
    py: Python<'py>,
    fragment: &[u8],
    width: u32,
    nchannels: u32,
    inrate: u32,
    outrate: u32,
    state: Option<RatecvState>,
) -> PyResult<(Bound<'py, PyBytes>, RatecvState)> {
    check_16bit_mono(width, nchannels)?;
    check_whole_frames(fragment)?;
    if inrate == 0 || outrate == 0 {
        return Err(PyValueError::new_err("sampling rate not > 0"));
    }
    let mut rc = dsp::RateCv::new(inrate, outrate);
    if let Some((d, ((prev, cur),))) = state {
        rc.set_state((d, prev, cur));
    }
    let mut out = Vec::new();
    rc.process(fragment, &mut out);
    let (d, prev, cur) = rc.state();
    Ok((PyBytes::new(py, &out), (d, ((prev, cur),))))
}

#[pymodule]
mod tt_audio_rs {
    #[pymodule_export]
    use super::{mul, ratecv};
}
