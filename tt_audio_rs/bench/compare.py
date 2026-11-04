"""
Python vs Rust audio pipeline comparison. Prints a Markdown report.

Run from the repo root (the hardware section opens the default mic and
speaker for a few minutes and only ever plays silence):
    python tt_audio_rs/bench/compare.py
    python tt_audio_rs/bench/compare.py --no-hardware
"""

import argparse
import audioop
import base64
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time

import pyaudio

import tt_audio_rs
from tt.utils.audio_interface import PyAudioInterface
from tt.utils.audio_interface_openai import AudioIO as PyAudioIO
from tt.utils.audio_interface_rust import RustAudioInterface


def per_call_us(fn, budget=0.4):
    """Median over 5 runs of the mean time per call, in microseconds."""
    n = 1
    while True:
        start = time.perf_counter()
        for _ in range(n):
            fn()
        if time.perf_counter() - start > budget / 5:
            break
        n *= 2
    runs = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(n):
            fn()
        runs.append((time.perf_counter() - start) / n * 1e6)
    return statistics.median(runs)


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def speedup(py_us, rs_us):
    return f"{py_us / rs_us:.1f}x"


# ---------------------------------------------------------------------------
# 1. DSP kernels: audioop (C) vs the Rust ports, called from Python
# ---------------------------------------------------------------------------


def bench_kernels():
    rows = []
    for label, data, factor in [
        ("mul, 1024-frame mic chunk (OpenAI ducking)", os.urandom(2048), 0.5623),
        ("mul, 4000-frame mic chunk (ElevenLabs ducking)", os.urandom(8000), 0.1),
    ]:
        py = per_call_us(lambda: audioop.mul(data, 2, factor))
        rs = per_call_us(lambda: tt_audio_rs.mul(data, 2, factor))
        rows.append((label, f"{py:.2f}", f"{rs:.2f}", speedup(py, rs)))
    for label, data in [
        ("ratecv 16k->48k, 100 ms chunk", os.urandom(3200)),
        ("ratecv 16k->48k, 500 ms chunk", os.urandom(16000)),
    ]:
        state = {"py": None, "rs": None}

        def py_call():
            _, state["py"] = audioop.ratecv(data, 2, 1, 16000, 48000, state["py"])

        def rs_call():
            _, state["rs"] = tt_audio_rs.ratecv(data, 2, 1, 16000, 48000, state["rs"])

        py, rs = per_call_us(py_call), per_call_us(rs_call)
        rows.append((label, f"{py:.2f}", f"{rs:.2f}", speedup(py, rs)))
    return table(["operation", "Python audioop (us)", "Rust (us)", "speedup"], rows)


# ---------------------------------------------------------------------------
# 2. Pipeline operations, driven through the same calls on both
# ---------------------------------------------------------------------------


def bench_pipeline():
    rows = []
    for seconds in (0.1, 1, 10, 60):
        backlog = bytes(int(24000 * 2 * seconds))
        results = []
        for make in (PyAudioIO, tt_audio_rs.AudioIO):
            io = make()
            io.push_tts(backlog)
            refill = bytes(2048)

            def step():
                io._spk_cb(None, 1024, None, 0)
                io.push_tts(refill)  # keep the backlog constant

            results.append(per_call_us(step))
            if make is PyAudioIO:
                io.p.terminate()
        rows.append((f"speaker callback + push_tts, {seconds:g} s of TTS buffered",
                     f"{results[0]:.2f}", f"{results[1]:.2f}", speedup(*results)))

    results = []
    mic = os.urandom(2048)
    for make in (PyAudioIO, tt_audio_rs.AudioIO):
        io = make()
        io.ducking = True

        def step():
            io._mic_cb(mic, 1024, None, 0)
            io.read_mic_chunk()

        results.append(per_call_us(step))
        if make is PyAudioIO:
            io.p.terminate()
    rows.append(("mic callback (ducked) + read_mic_chunk", f"{results[0]:.2f}",
                 f"{results[1]:.2f}", speedup(*results)))

    py, rs = PyAudioInterface(), RustAudioInterface()
    agent_audio = os.urandom(3200)  # 100 ms at 16 kHz

    def py_step():
        py.output(agent_audio)
        py.output_queue.get_nowait()

    def rs_step():
        rs.output(agent_audio)
        rs.audio._drain()

    results = [per_call_us(py_step), per_call_us(rs_step)]
    rows.append(("ElevenLabs output() of 100 ms + dequeue", f"{results[0]:.2f}",
                 f"{results[1]:.2f}", speedup(*results)))
    return table(["operation", "Python (us)", "Rust (us)", "speedup"], rows)


# ---------------------------------------------------------------------------
# 3. Data integrity: push_tts racing the speaker callback
# ---------------------------------------------------------------------------


def race(make, seconds=3.0):
    """Push marker bytes from one thread while another runs the speaker
    callback, then count how many markers were actually played."""
    io = make()
    stop = threading.Event()
    pushed = played = 0
    marker = b"\x01" * 960

    def producer():
        nonlocal pushed
        while not stop.is_set():
            if pushed - played > 4_000_000:  # bound the backlog (and Python's bytearray)
                time.sleep(0.001)
                continue
            io.push_tts(marker)
            pushed += len(marker)
            time.sleep(0)

    def consumer():
        nonlocal played
        while not stop.is_set():
            out, _ = io._spk_cb(None, 1024, None, 0)
            played += out[:2048].count(1)  # PortAudio only copies frame_count * 2 bytes
            time.sleep(0)

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    threads = [threading.Thread(target=producer), threading.Thread(target=consumer)]
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join()
    sys.setswitchinterval(previous)
    while True:  # play out whatever is still buffered
        out, _ = io._spk_cb(None, 1024, None, 0)
        if not out.count(1):
            break
        played += out[:2048].count(1)
    if make is PyAudioIO:
        io.p.terminate()
    return pushed, played


def bench_race():
    rows = []
    for name, make in (("Python", PyAudioIO), ("Rust", tt_audio_rs.AudioIO)):
        pushed, played = race(make)
        rows.append((name, f"{pushed:,}", f"{played:,}", f"{pushed - played:,}"))
    return table(["implementation", "TTS bytes pushed", "bytes played", "bytes lost"], rows)


# ---------------------------------------------------------------------------
# 4. Real devices while other Python threads keep the GIL busy
# ---------------------------------------------------------------------------


def burn_cpu(stop):
    """Pure-Python work, e.g. a busy tool call or heavy websocket parsing.
    Hands the GIL over every 5 ms (sys.getswitchinterval)."""
    while not stop.is_set():
        sum(i * i for i in range(2000))


def make_hold_gil(size=60_000):
    payload = json.dumps([{"id": i, "text": "memory highlight", "score": i / 7} for i in range(size)])
    start = time.perf_counter()
    json.loads(payload)
    held_ms = (time.perf_counter() - start) * 1e3

    def hold_gil(stop):
        """A C call that holds the GIL throughout (json.loads of a large API
        response), four times a second."""
        while not stop.is_set():
            json.loads(payload)
            time.sleep(0.25)

    return hold_gil, held_ms


def instrument(callback, records):
    """Wrap a PyAudio callback to record how long after PortAudio invoked it
    the Python code started and finished (both clocks are mach time)."""

    def wrapped(in_data, frame_count, time_info, status):
        entered = time.monotonic()
        result = callback(in_data, frame_count, time_info, status)
        records.append((entered - time_info["current_time"],
                        time.monotonic() - time_info["current_time"], status))
        return result

    return wrapped


def summarize_python(records):
    waits = [r[0] * 1e3 for r in records]
    return {
        "callbacks": len(records),
        "wait_p50_ms": statistics.median(waits),
        "wait_p99_ms": statistics.quantiles(waits, n=100, method="inclusive")[98],
        "wait_max_ms": max(waits),
        "done_max_ms": max(r[1] for r in records) * 1e3,
    }


def summarize_rust(stats):
    # The Rust callback never waits for the GIL; its whole run time is
    # measured inside the callback.
    return {
        "callbacks": stats["callbacks"],
        "wait_p50_ms": 0.0,
        "wait_p99_ms": 0.0,
        "wait_max_ms": 0.0,
        "done_max_ms": stats["max_us"] / 1e3,
    }


def start_threads(targets, stop):
    threads = [threading.Thread(target=t, args=(stop,), daemon=True) for t in targets]
    for t in threads:
        t.start()
    return threads


def run_openai(impl, load, seconds):
    io = PyAudioIO() if impl == "Python" else tt_audio_rs.AudioIO()
    speaker, mic = [], []
    if impl == "Python":
        io._spk_cb = instrument(io._spk_cb, speaker)
        io._mic_cb = instrument(io._mic_cb, mic)
    received = 0

    def tts_producer(stop):
        # Real-time pace in 20 ms frames, like the WebRTC transport.
        io.push_tts(bytes(14400))  # 300 ms prebuffer
        due = time.monotonic()
        while not stop.is_set():
            io.push_tts(bytes(960))
            due += 0.02
            time.sleep(max(0.0, due - time.monotonic()))

    def mic_consumer(stop):
        # Same loop as RealtimeConversation._mic_loop.
        nonlocal received
        while not stop.is_set():
            chunk = io.read_mic_chunk()
            if chunk:
                json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode()})
                received += 1
            time.sleep(0.01)

    stop = threading.Event()
    io.start()
    started = time.monotonic()
    threads = start_threads([tts_producer, mic_consumer] + load, stop)
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - started
    io.stop()
    if impl == "Python":
        return summarize_python(speaker), summarize_python(mic), elapsed
    stats = io.stats()
    return summarize_rust(stats["speaker"]), summarize_rust(stats["mic"]), elapsed


def run_elevenlabs(impl, load, seconds):
    iface = PyAudioInterface() if impl == "Python" else RustAudioInterface()
    mic = []
    if impl == "Python":
        iface._on_mic_data = instrument(iface._on_mic_data, mic)
    sent = 0
    ws, server = socket.socketpair()  # stands in for the ElevenLabs websocket
    ws.settimeout(2.0)  # never let a stuck send wedge an audio thread

    def input_callback(audio):
        # What the ElevenLabs SDK does with every mic chunk.
        nonlocal sent
        ws.sendall(json.dumps({"user_audio_chunk": base64.b64encode(audio).decode()}).encode())
        sent += 1

    def server_reader(stop):
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                server.recv(65536)
            except TimeoutError:
                pass

    def agent_audio(stop):
        due = time.monotonic()
        while not stop.is_set():
            iface.output(bytes(3200))  # 100 ms at 16 kHz
            due += 0.1
            time.sleep(max(0.0, due - time.monotonic()))

    # The reader outlives the audio streams: a mic callback blocked on a full
    # socket would otherwise hold up stopping the stream.
    reader_stop = threading.Event()
    reader = start_threads([server_reader], reader_stop)
    stop = threading.Event()
    iface.start(input_callback)
    started = time.monotonic()
    threads = start_threads([agent_audio] + load, stop)
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - started
    if impl == "Python":
        # PyAudioInterface.stop() raises AttributeError (self.should_stop),
        # so shut it down by hand once the play loop has gone idle.
        iface.in_stream.stop_stream()
        iface.in_stream.close()
        time.sleep(0.5)
        iface.stop_event.set()
        time.sleep(0.3)
        iface.out_stream.stop_stream()
        iface.out_stream.close()
        iface.p.terminate()
        result = summarize_python(mic), None, sent, elapsed
    else:
        iface.stop()
        stats = iface.audio.stats()
        result = summarize_rust(stats["mic"]), summarize_rust(stats["speaker"]), sent, elapsed
    reader_stop.set()
    for t in reader:
        t.join()
    ws.close()
    server.close()
    return result


def fmt_row(load, impl, stream, s, expected, note=""):
    got = f"{s['callbacks']} / {expected}" if expected else str(s["callbacks"])
    return (load, impl, stream, got, f"{s['wait_p50_ms']:.2f} / {s['wait_p99_ms']:.2f} / {s['wait_max_ms']:.2f}",
            f"{s['done_max_ms']:.2f}", note)


def bench_hardware(seconds):
    hold_gil, held_ms = make_hold_gil()
    loads = {
        "idle": [],
        "busy (1 CPU-bound thread)": [burn_cpu],
        f"heavy (3 CPU threads + {held_ms:.0f} ms GIL holds)": [burn_cpu] * 3 + [hold_gil],
    }
    openai_rows, eleven_rows = [], []
    for load_name, load in loads.items():
        for impl in ("Python", "Rust"):
            speaker, mic, elapsed = run_openai(impl, load, seconds)
            expected = round(elapsed * 24000 / 1024)
            openai_rows.append(fmt_row(load_name, impl, "speaker", speaker, expected))
            openai_rows.append(fmt_row(load_name, impl, "mic", mic, expected))
            mic, speaker, sent, elapsed = run_elevenlabs(impl, load, seconds)
            eleven_rows.append(fmt_row(load_name, impl, "mic", mic, round(elapsed * 4),
                                       f"{sent} chunks sent to input_callback"))
            if speaker:
                eleven_rows.append(fmt_row(load_name, impl, "speaker", speaker, None))
            print(f"  done: {load_name} / {impl}", file=sys.stderr)
    headers = ["load", "impl", "stream", "callbacks (got / expected)", "GIL wait p50 / p99 / max (ms)",
               "callback done by, max (ms)", "note"]
    return table(headers, openai_rows), table(headers, eleven_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-hardware", action="store_true", help="skip the real-device section")
    parser.add_argument("--seconds", type=float, default=10.0, help="duration of each real-device run")
    args = parser.parse_args()

    print("# Python vs Rust audio pipeline\n")
    cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout
    print(f"{cpu.strip() or platform.machine()} / macOS {platform.mac_ver()[0]} / "
          f"Python {platform.python_version()} / {pyaudio.get_portaudio_version_text()}\n")
    print("## DSP kernels (per call, from Python)\n")
    print(bench_kernels(), "\n")
    print("## Pipeline operations (per call, from Python)\n")
    print(bench_pipeline(), "\n")
    print("## push_tts racing the speaker callback (3 s, thread switch every 1 us)\n")
    print(bench_race(), "\n")
    if not args.no_hardware:
        openai, eleven = bench_hardware(args.seconds)
        print(f"## Real devices, {args.seconds:g} s per run\n")
        print("### OpenAI AudioIO (24 kHz, 1024-frame callbacks)\n")
        print(openai, "\n")
        print("### ElevenLabs interface (16 kHz mic, 4000-frame callbacks)\n")
        print(eleven, "\n")


if __name__ == "__main__":
    main()
