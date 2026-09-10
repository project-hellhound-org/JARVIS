import threading
from typing import Callable, Dict, List, Any

class EventBus:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(EventBus, cls).__new__(cls)
                cls._instance._listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
                cls._instance._bridge_callback: Callable[[str, Dict[str, Any]], None] = None
            return cls._instance

    def set_bridge_callback(self, callback: Callable[[str, Dict[str, Any]], None]):
        """Register the desktop PyWebview _emit callback."""
        self._bridge_callback = callback

    def subscribe(self, event_name: str, callback: Callable[[Dict[str, Any]], None]):
        """Subscribe internal Python handler to an event."""
        with self._lock:
            if event_name not in self._listeners:
                self._listeners[event_name] = []
            if callback not in self._listeners[event_name]:
                self._listeners[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback: Callable[[Dict[str, Any]], None]):
        with self._lock:
            if event_name in self._listeners and callback in self._listeners[event_name]:
                self._listeners[event_name].remove(callback)

    def emit(self, event_name: str, payload: Dict[str, Any]):
        """
        Emit event locally and forward to PyWebview JS frontend via bridge.
        """
        # Call Python subscribers
        with self._lock:
            listeners = list(self._listeners.get(event_name, []))
            all_listeners = list(self._listeners.get("*", []))

        for listener in listeners + all_listeners:
            try:
                listener(payload)
            except Exception as e:
                print(f"[event_bus] Listener error for '{event_name}': {e}")

        # Forward over PyWebview bridge
        if self._bridge_callback:
            try:
                self._bridge_callback("jarvis_task_event", {
                    "event": event_name,
                    "payload": payload
                })
            except Exception as e:
                print(f"[event_bus] PyWebview bridge emit error for '{event_name}': {e}")

def get_event_bus() -> EventBus:
    return EventBus()
