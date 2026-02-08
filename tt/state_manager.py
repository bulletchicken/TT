"""
Centralized state manager for the TT system.

Coordinates software (wake word, conversation) and firmware (sensors, servos)
through a simple state machine:

    IDLE  ⇄  ACTIVE  →  WINDING_DOWN  →  IDLE

Usage:
    from tt.state_manager import StateManager, State

    sm = StateManager()
    sm.on_change(my_callback)
    sm.transition(State.ACTIVE)
"""

import enum
import threading


class State(enum.Enum):
    IDLE = "IDLE"                    # Wake word listening; FW sensors/servos off
    ACTIVE = "ACTIVE"                # Conversation in progress; FW everything on
    WINDING_DOWN = "WINDING_DOWN"    # Saving logs, cleaning up; transitioning to IDLE


class StateManager:
    """Thread-safe state machine with observer callbacks and FW serial bridge."""

    def __init__(self):
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._listeners: list[callable] = []

        # FUTURE FW integration: serial connection to microcontroller
        # self._fw_serial = None

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def transition(self, new_state: State):
        """
        Move to a new state. Notifies firmware and all registered listeners.
        Thread-safe — can be called from any thread (mic loop, WS recv, main).
        """
        with self._lock:
            old = self._state
            if old == new_state:
                return
            self._state = new_state

        print(f"[StateManager] {old.value} → {new_state.value}")
        self._notify_fw(new_state)

        for cb in self._listeners:
            try:
                cb(old, new_state)
            except Exception as e:
                print(f"[StateManager] Listener error: {e}")

    def on_change(self, callback):
        """
        Register a callback: callback(old_state, new_state).
        Called synchronously on the thread that triggered the transition.
        """
        self._listeners.append(callback)

    # -----------------------------------------------------------------
    # Firmware communication
    # -----------------------------------------------------------------

    def _notify_fw(self, state: State):
        """
        Send the current state to firmware over serial.

        FUTURE FW integration: uncomment and configure once the serial
        link to the microcontroller is set up. The firmware should parse
        simple newline-terminated commands:

            "IDLE\n"          → cut servo PWM (arms free), disable MPU I2C,
                                power down heart rate sensor
            "ACTIVE\n"        → enable servo PWM, enable MPU I2C,
                                power up heart rate sensor
            "WINDING_DOWN\n"  → begin graceful sensor shutdown (same as IDLE
                                but firmware can ramp down smoothly)
        """
        # if self._fw_serial and self._fw_serial.is_open:
        #     self._fw_serial.write(f"{state.value}\n".encode())
        pass

    # def connect_firmware(self, port="/dev/ttyUSB0", baud=115200):
    #     """Open serial connection to firmware microcontroller."""
    #     import serial
    #     self._fw_serial = serial.Serial(port, baud, timeout=1)
    #     print(f"[StateManager] Firmware connected on {port} @ {baud}")
