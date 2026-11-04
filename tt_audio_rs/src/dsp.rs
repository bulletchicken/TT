//! Bit-exact ports of the two `audioop` kernels the Python pipeline uses.
//!
//! Both work on native-endian 16-bit mono PCM (PortAudio's paInt16 layout,
//! which is also what `audioop` reads), so their output matches the Python
//! implementation byte for byte.

/// `audioop.mul(fragment, 2, factor)`, in place.
///
/// Same arithmetic as CPython: multiply in f64, clamp to the int16 range
/// (anything below -32767.0 becomes -32768), then round toward -inf.
pub fn mul(pcm: &mut [u8], factor: f64) {
    for s in pcm.chunks_exact_mut(2) {
        let v = f64::from(i16::from_ne_bytes([s[0], s[1]])) * factor;
        let v = if v > 32767.0 {
            32767.0
        } else if v < -32767.0 {
            -32768.0
        } else {
            v
        };
        s.copy_from_slice(&(v.floor() as i16).to_ne_bytes());
    }
}

/// Streaming port of `audioop.ratecv(fragment, 2, 1, inrate, outrate, state)`
/// (mono 16-bit, default weights). It keeps audioop's `(d, ((prev, cur),))`
/// state between calls, so a chunked stream resamples exactly as it does in
/// Python with the state threaded through.
#[derive(Clone, Debug, PartialEq)]
pub struct RateCv {
    inrate: i32,
    outrate: i32,
    d: i32,
    prev: i32,
    cur: i32,
}

impl RateCv {
    pub fn new(inrate: u32, outrate: u32) -> Self {
        let g = gcd(inrate, outrate);
        let outrate = (outrate / g) as i32;
        Self {
            inrate: (inrate / g) as i32,
            outrate,
            d: -outrate,
            prev: 0,
            cur: 0,
        }
    }

    /// Back to the `state=None` starting point.
    pub fn reset(&mut self) {
        self.d = -self.outrate;
        self.prev = 0;
        self.cur = 0;
    }

    /// `(d, prev, cur)`, the values audioop packs into its state tuple.
    pub fn state(&self) -> (i32, i32, i32) {
        (self.d, self.prev, self.cur)
    }

    pub fn set_state(&mut self, (d, prev, cur): (i32, i32, i32)) {
        self.d = d;
        self.prev = prev;
        self.cur = cur;
    }

    /// Resample `input` and append the result to `out`.
    pub fn process(&mut self, input: &[u8], out: &mut Vec<u8>) {
        let (inrate, outrate) = (self.inrate, self.outrate);
        let frames = input.len() / 2;
        if frames > 0 {
            // audioop's upper bound: ceil(frames / inrate) * outrate.
            out.reserve((1 + (frames - 1) / inrate as usize) * outrate as usize * 2);
        }
        let mut samples = input.chunks_exact(2);
        let (mut d, mut prev, mut cur) = (self.d, self.prev, self.cur);
        'stream: loop {
            while d < 0 {
                let Some(s) = samples.next() else {
                    break 'stream;
                };
                // With the default weights (1, 0) audioop's smoothing filter is
                // the identity, so it is left out.
                prev = cur;
                cur = i32::from(i16::from_ne_bytes([s[0], s[1]])) << 16;
                d += outrate;
            }
            while d >= 0 {
                let o = (f64::from(prev) * f64::from(d) + f64::from(cur) * f64::from(outrate - d))
                    / f64::from(outrate);
                out.extend_from_slice(&(((o as i32) >> 16) as i16).to_ne_bytes());
                d -= inrate;
            }
        }
        self.d = d;
        self.prev = prev;
        self.cur = cur;
    }
}

fn gcd(mut a: u32, mut b: u32) -> u32 {
    while b > 0 {
        (a, b) = (b, a % b);
    }
    a
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pcm(samples: &[i16]) -> Vec<u8> {
        samples.iter().flat_map(|s| s.to_ne_bytes()).collect()
    }

    fn samples(pcm: &[u8]) -> Vec<i16> {
        pcm.chunks_exact(2)
            .map(|s| i16::from_ne_bytes([s[0], s[1]]))
            .collect()
    }

    #[test]
    fn mul_floors_and_clamps_like_audioop() {
        let mut buf = pcm(&[1, -1, 100, 32767, -32768]);
        mul(&mut buf, 0.5623);
        assert_eq!(samples(&buf), [0, -1, 56, 18424, -18426]);

        let mut buf = pcm(&[30000, -30000, -16384, 16384]);
        mul(&mut buf, 2.0);
        assert_eq!(samples(&buf), [32767, -32768, -32768, 32767]);
    }

    #[test]
    fn ratecv_upsamples_by_linear_interpolation() {
        // 16 kHz -> 48 kHz reduces to 1:3. The first input sample yields one
        // output sample; every later one yields three.
        let mut rc = RateCv::new(16000, 48000);
        let mut out = Vec::new();
        rc.process(&pcm(&[3, 6]), &mut out);
        assert_eq!(samples(&out), [3, 4, 5, 6]);
        assert_eq!(rc.state(), (-1, 3 << 16, 6 << 16));

        rc.reset();
        assert_eq!(rc.state(), (-3, 0, 0));
    }

    #[test]
    fn ratecv_is_independent_of_chunking() {
        let input: Vec<u8> = (0..4801u32)
            .flat_map(|i| (((i * 7919) % 65536) as u16 as i16).to_ne_bytes())
            .collect();
        for (inrate, outrate) in [(16000, 48000), (16000, 44100), (48000, 16000), (24000, 48000)] {
            let mut whole = RateCv::new(inrate, outrate);
            let mut expected = Vec::new();
            whole.process(&input, &mut expected);

            let mut chunked = RateCv::new(inrate, outrate);
            let mut got = Vec::new();
            for chunk in input.chunks(2 * 37) {
                chunked.process(chunk, &mut got);
            }
            assert_eq!(got, expected, "{inrate} -> {outrate}");
            assert_eq!(chunked, whole);
        }
    }
}
