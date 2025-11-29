import json
import threading

from websocket import create_connection, WebSocketConnectionClosedException


class RealtimeSocket:
    """WebSocket wrapper for OpenAI Realtime API."""
    
    def __init__(self, api_key, ws_url, on_msg):
        self.api_key = api_key
        self.ws_url = ws_url
        self.ws = None
        self.on_msg = on_msg
        self._stop_event = threading.Event()
        self.lock = threading.Lock()

    def connect(self):
        self.ws = create_connection(
            self.ws_url,
            header=[
                f"Authorization: Bearer {self.api_key}",
                "OpenAI-Beta: realtime=v1",
            ],
        )
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        while not self._stop_event.is_set():
            try:
                raw = self.ws.recv()
                if raw and self.on_msg:
                    self.on_msg(json.loads(raw))
            except WebSocketConnectionClosedException:
                break
            except Exception as e:
                print("Receive error:", e)
                break

    def send(self, obj: dict):
        try:
            with self.lock:
                self.ws.send(json.dumps(obj))
        except Exception as e:
            print("Send error:", e)

    def close(self):
        self._stop_event.set()
        try:
            self.ws.send_close()
            self.ws.close()
        except:
            pass

