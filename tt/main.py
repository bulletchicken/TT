"""
Main lifecycle loop for the TT system.

Cycles through:  IDLE (wake word) → ACTIVE (conversation) → WINDING_DOWN → IDLE

Usage:
    python -m tt.main                    # default: elevenlabs backend
    python -m tt.main --backend openai   # use OpenAI Realtime API
"""

import argparse
import sys

from tt.state_manager import State, StateManager


def run(backend: str = "elevenlabs"):
    state_mgr = StateManager()

    # Lazy imports so we only load the backend we need
    if backend == "openai":
        from tt.brain.prefrontal_cortex.openai_realtime import RealtimeConversation
        from tt.config import OPENAI_API_KEY
    elif backend == "elevenlabs":
        from tt.brain.prefrontal_cortex.elevenlabs_realtime import play_audio
    else:
        print(f"Unknown backend: {backend}")
        sys.exit(1)

    print(f"[main] TT starting with {backend} backend. Ctrl+C to quit.")

    try:
        while True:
            # ---- IDLE: listen for wake word ----
            state_mgr.transition(State.IDLE)

            from tt.ears.wakeword_start import wait_for_wakeword
            wait_for_wakeword()

            # ---- ACTIVE: run conversation ----
            state_mgr.transition(State.ACTIVE)

            if backend == "openai":
                conv = RealtimeConversation(OPENAI_API_KEY, state_mgr)
                conv.start()
                conv.wait_until_done()
                conv.stop()  # saves log, triggers WINDING_DOWN via state_mgr
            else:
                play_audio(state_mgr)  # blocks until session ends, triggers WINDING_DOWN

            # ---- WINDING_DOWN → back to top of loop (IDLE) ----
            print("[main] Conversation ended. Returning to idle...\n")

    except KeyboardInterrupt:
        print("\n[main] Shutting down.")
        state_mgr.transition(State.IDLE)


def main():
    parser = argparse.ArgumentParser(description="TT main lifecycle loop")
    parser.add_argument(
        "--backend",
        choices=["openai", "elevenlabs"],
        default="elevenlabs",
        help="Conversation backend to use (default: elevenlabs)",
    )
    args = parser.parse_args()
    run(backend=args.backend)


if __name__ == "__main__":
    main()
