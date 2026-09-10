# modules/smart_home.py
"""
Smart Home & IoT Controls Module for J.A.R.V.I.S..
Provides controls for lights, thermostat, locks, and arrival routines ("I'm home, you beautiful bastard").
"""

import os
import json
from typing import Dict, Any

SMART_HOME_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "smart_home_state.json")


class SmartHomeManager:
    def __init__(self, filepath: str = SMART_HOME_FILE):
        self.filepath = filepath
        self._ensure_storage()

    def _ensure_storage(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            initial_state = {
                "lights": {
                    "state": "off",
                    "brightness": 100,
                    "color": "warm_cream"
                },
                "thermostat": {
                    "target_temp": 72,
                    "mode": "heat",
                    "current_temp": 68
                },
                "locks": {
                    "front_door": "locked",
                    "back_door": "locked"
                },
                "arrival_routine": "active"
            }
            self._save(initial_state)

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, state: Dict[str, Any]):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[smart_home] Error saving smart home state: {e}")

    def set_light_state(self, on: bool, brightness: int = 100) -> str:
        state = self._load()
        state.setdefault("lights", {})["state"] = "on" if on else "off"
        state["lights"]["brightness"] = brightness
        self._save(state)
        status = f"turned {'ON' if on else 'OFF'} (brightness: {brightness}%)"
        return f"Lights {status}, partner."

    def set_thermostat(self, temp: int) -> str:
        state = self._load()
        state.setdefault("thermostat", {})["target_temp"] = temp
        state["thermostat"]["current_temp"] = temp
        self._save(state)
        return f"Thermostat cranked to {temp}°F. Cozy as hell."

    def set_lock_state(self, lock_door: bool) -> str:
        state = self._load()
        lock_val = "locked" if lock_door else "unlocked"
        state.setdefault("locks", {})["front_door"] = lock_val
        self._save(state)
        if lock_door:
            return "Front door locked up tight, buddy. No unwanted guests getting through."
        else:
            return "Front door unlocked. Come on in."

    def handle_arrival(self) -> str:
        """Arrival routine triggered by 'I'm home, you beautiful bastard'."""
        self.set_light_state(on=True, brightness=100)
        self.set_thermostat(72)
        self.set_lock_state(lock_door=False)
        return "Cranked the heat to 72°, turned on the lights, and unlocked the door for you. Welcome home, you beautiful bastard!"
