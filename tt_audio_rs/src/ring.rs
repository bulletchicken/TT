//! Lock-free single-producer/single-consumer byte ring for PCM audio.
//!
//! One side of every ring is a PortAudio callback, which runs on CoreAudio's
//! real-time thread and must never wait on a lock. (The Python pipeline shares
//! a bytearray/list with that thread instead, so each callback first has to
//! acquire the GIL.)

use std::cell::UnsafeCell;
use std::sync::atomic::AtomicUsize;
use std::sync::atomic::Ordering::{Acquire, Relaxed, Release};

/// Bytes are addressed by ever-increasing positions: `head` is advanced only
/// by the producer and `tail` only by the consumer. `clear()` lets the
/// producer drop everything it has written so far; the consumer skips ahead
/// to `clear_to` on its next read.
pub struct Ring {
    buf: Box<[UnsafeCell<u8>]>,
    head: AtomicUsize,
    tail: AtomicUsize,
    clear_to: AtomicUsize,
}

// SAFETY: the consumer only reads slots in [tail, head) and the producer only
// writes slots outside that range; head/tail hand slots over with
// release/acquire ordering.
unsafe impl Sync for Ring {}

impl Ring {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0);
        // A zeroed allocation, so pages are only committed as the ring fills.
        let buf = vec![0u8; capacity].into_boxed_slice();
        // SAFETY: UnsafeCell<u8> has the same layout as u8.
        let buf = unsafe { Box::from_raw(Box::into_raw(buf) as *mut [UnsafeCell<u8>]) };
        Self {
            buf,
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
            clear_to: AtomicUsize::new(0),
        }
    }

    /// Producer: append as much of `data` as fits. Returns the bytes written.
    pub fn push(&self, data: &[u8]) -> usize {
        let head = self.head.load(Relaxed);
        let tail = self.tail.load(Acquire);
        let n = data.len().min(self.buf.len() - (head - tail));
        let (start, first) = self.span(head, n);
        let base = UnsafeCell::raw_get(self.buf.as_ptr());
        // SAFETY: [head, head + n) is free, so the consumer isn't reading it,
        // and both segments lie inside the buffer.
        unsafe {
            std::ptr::copy_nonoverlapping(data.as_ptr(), base.add(start), first);
            std::ptr::copy_nonoverlapping(data.as_ptr().add(first), base, n - first);
        }
        self.head.store(head + n, Release);
        n
    }

    /// Producer: discard everything pushed so far.
    pub fn clear(&self) {
        self.clear_to.store(self.head.load(Relaxed), Release);
    }

    /// Consumer: bytes available to read.
    pub fn len(&self) -> usize {
        let tail = self.tail.load(Relaxed).max(self.clear_to.load(Acquire));
        self.head.load(Acquire) - tail
    }

    /// Consumer: move up to `out.len()` bytes into `out`. Returns the count.
    pub fn pop(&self, out: &mut [u8]) -> usize {
        // clear_to must be read before head: the producer published head
        // first, so this guarantees head >= clear_to.
        let tail = self.tail.load(Relaxed).max(self.clear_to.load(Acquire));
        let n = out.len().min(self.head.load(Acquire) - tail);
        let (start, first) = self.span(tail, n);
        let base = UnsafeCell::raw_get(self.buf.as_ptr());
        // SAFETY: [tail, tail + n) was published by the producer and won't be
        // overwritten until tail moves past it; both segments lie inside the
        // buffer.
        unsafe {
            std::ptr::copy_nonoverlapping(base.add(start), out.as_mut_ptr(), first);
            std::ptr::copy_nonoverlapping(base, out.as_mut_ptr().add(first), n - first);
        }
        self.tail.store(tail + n, Release);
        n
    }

    /// Buffer index of `pos`, and how many of the `n` bytes fit before the
    /// end of the buffer (the rest wrap around to index 0).
    fn span(&self, pos: usize, n: usize) -> (usize, usize) {
        let start = pos % self.buf.len();
        (start, n.min(self.buf.len() - start))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn fifo_with_wraparound() {
        let ring = Ring::new(8);
        let mut out = [0u8; 8];
        assert_eq!(ring.push(&[1, 2, 3, 4, 5, 6]), 6);
        assert_eq!(ring.pop(&mut out[..4]), 4);
        assert_eq!(out[..4], [1, 2, 3, 4]);
        // Wraps around the end of the buffer.
        assert_eq!(ring.push(&[7, 8, 9, 10, 11]), 5);
        assert_eq!(ring.len(), 7);
        assert_eq!(ring.pop(&mut out), 7);
        assert_eq!(out[..7], [5, 6, 7, 8, 9, 10, 11]);
        assert_eq!(ring.pop(&mut out), 0);
    }

    #[test]
    fn push_stops_when_full() {
        let ring = Ring::new(4);
        assert_eq!(ring.push(&[1, 2, 3, 4, 5, 6]), 4);
        assert_eq!(ring.push(&[7]), 0);
        let mut out = [0u8; 2];
        ring.pop(&mut out);
        assert_eq!(ring.push(&[7, 8, 9]), 2);
    }

    #[test]
    fn clear_drops_only_what_was_pushed_before_it() {
        let ring = Ring::new(16);
        ring.push(&[1, 2, 3]);
        ring.clear();
        assert_eq!(ring.len(), 0);
        ring.push(&[4, 5]);
        let mut out = [0u8; 8];
        assert_eq!(ring.pop(&mut out), 2);
        assert_eq!(out[..2], [4, 5]);
    }

    #[test]
    fn concurrent_producer_and_consumer_keep_the_byte_sequence() {
        const TOTAL: usize = 4_000_000;
        let ring = Arc::new(Ring::new(1021));
        let producer = {
            let ring = Arc::clone(&ring);
            std::thread::spawn(move || {
                let (mut sent, mut size) = (0usize, 1usize);
                while sent < TOTAL {
                    size = size * 7 % 509 + 1;
                    let chunk: Vec<u8> = (sent..(sent + size).min(TOTAL)).map(|i| i as u8).collect();
                    let mut off = 0;
                    while off < chunk.len() {
                        off += ring.push(&chunk[off..]);
                    }
                    sent += chunk.len();
                }
            })
        };
        let (mut received, mut size) = (0usize, 3usize);
        let mut out = vec![0u8; 512];
        while received < TOTAL {
            size = size * 5 % 511 + 1;
            let n = ring.pop(&mut out[..size]);
            for (i, b) in out[..n].iter().enumerate() {
                assert_eq!(*b, (received + i) as u8, "byte {}", received + i);
            }
            received += n;
        }
        producer.join().unwrap();
        assert_eq!(ring.len(), 0);
    }
}
