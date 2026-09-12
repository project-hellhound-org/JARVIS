# frontend/desktop.py
import sys
import yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

try:
    import webview
except ImportError:
    webview = None
import asyncio
import threading
import queue
import json
import re
import subprocess
import time
import os

# Unrestrict audio autoplay in QtWebEngine so neural voice responses play without requiring a user click
current_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
required_flags = "--autoplay-policy=no-user-gesture-required --no-sandbox"
for f in required_flags.split():
    if f not in current_flags:
        current_flags = f"{current_flags} {f}".strip()
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = current_flags

import base64
import tempfile
import shutil
import struct
import wave
try:
    import psutil
except ImportError:
    psutil = None
# Suppress C-level ALSA / JACK warning log spam in terminal
try:
    from ctypes import CFUNCTYPE, c_char_p, c_int, cdll
    _ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
    def _py_error_handler(filename, line, function, err, fmt):
        pass
    _c_error_handler = _ERROR_HANDLER_FUNC(_py_error_handler)
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(_c_error_handler)
except Exception:
    pass

try:
    import speech_recognition as sr
except ImportError:
    sr = None
from core.target_model import Target
from core.case_brief import CaseBrief, parse_brief_with_slm
from core.wake_word import WakeWordEngine
from narrative.jarvis_voice import JarvisVoice, JarvisVoice
from narrative.session_memory import SessionMemory
from memory.lessons_store import LessonsStore

ROOT = Path(__file__).parent
HTML_PATH = ROOT / "app.html"
GEV_DIR = ROOT.parent / "gods_eye"
GEV_PORT = 4173

import atexit
import signal

# Keywords that signal extra context beyond just the target
_CONTEXT_SIGNALS = re.compile(
    r"(work|employ|company|corp|repo|github|leak|old|suspect|ctf|"
    r"used to|might have|previously|formerly|known as)",
    re.IGNORECASE,
)

class GEVServer:
    """Manages the God's Eye View Vite dev server as a background subprocess."""
    _instance = None

    def __init__(self, gev_dir=None, port=None):
        self.gev_dir = str(gev_dir or GEV_DIR)
        self.port = port or GEV_PORT
        self.process = None
        self._started = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self):
        """Start the GEV Vite dev server if not already running."""
        if self._started and self.process and self.process.poll() is None:
            return True

        gev_path = Path(self.gev_dir)
        if not (gev_path / "package.json").exists():
            print(f"[GEV] God's Eye View directory not found: {self.gev_dir}")
            return False

        try:
            env = dict(os.environ)
            env_file = gev_path / ".env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip()

            self.process = subprocess.Popen(
                ["npx", "vite", "--port", str(self.port), "--host", "127.0.0.1", "--strictPort"],
                cwd=self.gev_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            self._started = True
            atexit.register(self.stop)
            print(f"[GEV] God's Eye View OSINT server started on http://127.0.0.1:{self.port}")
            return True
        except FileNotFoundError:
            print("[GEV] npx/node not found. Ensure Node.js 24+ is installed.")
            return False
        except Exception as e:
            print(f"[GEV] Failed to start server: {e}")
            return False

    def stop(self):
        """Terminate the GEV server process and its process group."""
        if self.process:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                else:
                    self.process.terminate()
            except (ProcessLookupError, OSError):
                pass
            self.process = None
            self._started = False
            print("[GEV] God's Eye View server stopped.")

    def is_running(self):
        return self._started and self.process and self.process.poll() is None

    def get_url(self):
        return f"http://127.0.0.1:{self.port}" if self.is_running() else ""


class _StreamingPrefixFilter:
    CANDIDATES = ("JARVIS:", "J.A.R.V.I.S.:", "ASSISTANT:", "AI:")
    CLEAN_REGEX = re.compile(r'^\s*(?:JARVIS|J\.A\.R\.V\.I\.S\.|ASSISTANT|AI)\s*:\s*', re.IGNORECASE)

    def __init__(self, on_chunk):
        self.on_chunk = on_chunk
        self.buffer = ""
        self.cleared = False
        self.cmd_buffer = ""
        self.capturing_cmd = False
        self.cmd_in_single = False
        self.cmd_in_double = False
        self.action_buffer = ""
        self.capturing_action = False

    def push(self, chunk: str):
        if not chunk:
            return

        if not self.cleared:
            self.buffer += chunk
            clean_buf = self.buffer.lstrip()

            m = self.CLEAN_REGEX.match(clean_buf)
            if m:
                self.cleared = True
                remaining = clean_buf[m.end():]
                self.buffer = ""
                if remaining:
                    self._feed_text(remaining)
                return

            upper = clean_buf.upper()
            could_match = any(cand.startswith(upper) for cand in self.CANDIDATES)
            if not could_match or len(clean_buf) > 25:
                self.cleared = True
                to_flush = self.buffer
                self.buffer = ""
                self._feed_text(to_flush)
            return

        self._feed_text(chunk)

    feed = push

    def _feed_text(self, text: str):
        # Filter out [CMD: <cmd>] directives and *stage directions* on the fly
        for ch in text:
            if not self.capturing_cmd and not self.capturing_action:
                if ch == '[':
                    self.capturing_cmd = True
                    self.cmd_buffer = '['
                    self.cmd_in_single = False
                    self.cmd_in_double = False
                elif ch == '*':
                    self.capturing_action = True
                    self.action_buffer = '*'
                else:
                    self.on_chunk(ch)
            elif self.capturing_action:
                self.action_buffer += ch
                if ch == '*':
                    self.capturing_action = False
                    # Action *...* (*grins*, *cracks knuckles*, etc.) is discarded completely!
                    self.action_buffer = ""
                elif len(self.action_buffer) > 100 or ch == '\n':
                    # Overflow: not a short stage direction
                    self.capturing_action = False
                    self.on_chunk(self.action_buffer)
                    self.action_buffer = ""
            elif self.capturing_cmd:
                self.cmd_buffer += ch
                if ch == "'" and not self.cmd_in_double:
                    self.cmd_in_single = not self.cmd_in_single
                elif ch == '"' and not self.cmd_in_single:
                    self.cmd_in_double = not self.cmd_in_double
                elif ch == ']' and not self.cmd_in_single and not self.cmd_in_double:
                    self.capturing_cmd = False
                    self.cmd_in_single = False
                    self.cmd_in_double = False
                    # Check if this bracket block is a CMD directive
                    m = re.match(r'\[CMD(?::|\s)\s*(.+)\]\s*$', self.cmd_buffer, re.IGNORECASE | re.DOTALL)
                    if m:
                        cmd_to_run = m.group(1).strip()
                        try:
                            from core.system_commander import get_system_commander
                            commander = get_system_commander()
                            commander.run_as_task(cmd_to_run, title=f"Terminal: {cmd_to_run[:30]}")
                        except Exception as e:
                            print(f"[desktop] Streaming command launch error: {e}")
                    else:
                        # Not a CMD tag (e.g. markdown link or reference), pass through
                        self.on_chunk(self.cmd_buffer)
                    self.cmd_buffer = ""
                elif len(self.cmd_buffer) > 2000 or (ch == '\n' and not self.cmd_in_single and not self.cmd_in_double and len(self.cmd_buffer) > 400):
                    # Overflow protection: not a reasonable CMD tag
                    self.capturing_cmd = False
                    self.cmd_in_single = False
                    self.cmd_in_double = False
                    self.on_chunk(self.cmd_buffer)
                    self.cmd_buffer = ""

    def flush(self):
        if not self.cleared and self.buffer:
            clean_buf = self.CLEAN_REGEX.sub('', self.buffer.lstrip())
            self.cleared = True
            self.buffer = ""
            if clean_buf:
                self._feed_text(clean_buf)
        if self.capturing_action and self.action_buffer:
            self.capturing_action = False
            # Drop trailing action tag if cut off mid-stream
            if not any(w in self.action_buffer.lower() for w in ["chuckle", "grin", "smirk", "laugh", "sigh", "knuckle", "lean", "crack"]):
                self.on_chunk(self.action_buffer)
            self.action_buffer = ""
        if self.capturing_cmd and self.cmd_buffer:
            self.capturing_cmd = False
            m = re.match(r'\[CMD(?::|\s)\s*([^\]]+)', self.cmd_buffer, re.IGNORECASE)
            if m:
                cmd_to_run = m.group(1).strip()
                try:
                    from core.system_commander import get_system_commander
                    commander = get_system_commander()
                    commander.run_as_task(cmd_to_run, title=f"Terminal: {cmd_to_run[:30]}")
                except Exception:
                    pass
            else:
                self.on_chunk(self.cmd_buffer)
            self.cmd_buffer = ""


class JarvisAPI:
    def __init__(self):
        self._window = None
        self._target: Target = None
        self._memory = SessionMemory()
        self._voice = JarvisVoice()
        self._lessons_store = LessonsStore()
        self._orch = None
        self._stalk_loop = None
        self._stalk_task = None
        self._last_entity = None  # Track for false-positive command
        self._wake_window_expires = 0.0
        self._recent_agent_responses = []
        self._tts_playback_until = 0.0
        self._tts_turn_lock = threading.Lock()
        self._tts_turn_id = 0
        self._current_tts_proc = None
        self._telemetry_last_net = None
        self._telemetry_last_time = None
        self._shared_audio_queue = queue.Queue(maxsize=150)
        self._cfg = self._load_config()

        # Async Background Initialization for Instant App Launch (<0.2s)
        self._wake_engine = None
        threading.Thread(target=self._async_init_wake_engine, daemon=True).start()

    def _load_config(self) -> dict:
        try:
            import yaml
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r") as f:
                    return yaml.safe_load(f) or {}
        except Exception:
            pass
        return {}

    def get_gev_url(self) -> str:
        """Return God's Eye View server URL, auto-starting the server if not already running."""
        gev = GEVServer.get_instance()
        if not gev.is_running():
            gev.start()
        return gev.get_url()

    def _on_shared_audio_chunk(self, chunk: bytes):
        """Unified audio capture callback fed directly from WakeWordEngine's active PyAudio stream."""
        try:
            self._shared_audio_queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._shared_audio_queue.get_nowait()
                self._shared_audio_queue.put_nowait(chunk)
            except Exception:
                pass

    def _async_init_wake_engine(self):
        try:
            print("[desktop] Spinning up openWakeWord engine in background...")
            self._wake_engine = WakeWordEngine(
                on_wake_detected=self._on_wake_word_detected,
                on_speech_ended=self._on_vad_speech_ended,
                audio_chunk_callback=self._on_shared_audio_chunk,
                threshold=0.35,
                silence_timeout_sec=3.2,
                max_window_sec=30.0
            )
            self._wake_engine.start()
            print("[desktop] Background wake engine ready.")
        except Exception as e:
            print(f"[desktop] Background wake engine init notice: {e}")

    def _on_wake_word_detected(self, phrase: str):
        if time.time() < getattr(self, '_tts_playback_until', 0.0):
            print(f"[desktop] openWakeWord triggered ('{phrase}') during active TTS playback — suppressed to prevent acoustic echo loop.")
            return
        print(f"[desktop] openWakeWord triggered ('{phrase}'). Opening STT command capture window.")
        now = time.time()
        self._wake_window_expires = now + 20.0
        self._emit("jarvis_wake_word_detected", {"raw": phrase, "clean": ""})

    def _on_vad_speech_ended(self):
        print("[desktop] VAD detected post-speech silence. Closing STT command window early.")
        self._wake_window_expires = 0.0
        self._emit("jarvis_vad_speech_end", {})

    def _get_orchestrator(self):
        if self._orch is None:
            from core.orchestrator import Orchestrator
            self._orch = Orchestrator(
                on_status=self._on_status,
                on_find=self._on_find,
                on_done=lambda t: self._on_done(t, aborted=False),
                lessons_store=self._lessons_store,
            )
        return self._orch

    def set_window(self, window):
        self._window = window
        try:
            from core.event_bus import get_event_bus
            get_event_bus().set_bridge_callback(self._emit)
        except Exception as e:
            print(f"[desktop] EventBus bridge hook error: {e}")
        try:
            from core.system_commander import get_system_commander
            get_system_commander().set_debrief_callback(self._on_command_debrief)
        except Exception as e:
            print(f"[desktop] SystemCommander debrief hook error: {e}")

    def process_input(self, text: str):
        norm = re.sub(r'[^\w\s]', '', text or '').strip().lower()
        now = time.time()
        last_text, last_time = getattr(self, '_last_input_seen', ('', 0.0))
        if norm and norm == last_text and (now - last_time) < 2.5:
            print(f"[desktop] Debouncing duplicate input '{text}' received within {now - last_time:.2f}s")
            return
        self._last_input_seen = (norm, now)

        print(f"\n[desktop] Unified AI input received: {text}")
        if self._wake_engine:
            try:
                self._wake_engine.reset_cooldown(2.0)
            except Exception:
                pass
        self._memory.add("user", text)
        threading.Thread(target=self._run_process_input, args=(text,), daemon=True).start()

    def _on_skill_progress(self, finfo):
        if isinstance(finfo, dict) and "file" in finfo:
            msg_str = f"⚡ LIVE CODE AUDIT [{finfo['index']}/{finfo['total']}]: {finfo['file']} ({finfo['lines']} LOC)... [VERIFIED]"
            self._emit("jarvis_stt_interim", {"text": msg_str})

    def _resolve_and_glide_location(self, text: str) -> bool:
        """Detect location queries and glide 3D camera to Earth globe with coordinates."""
        pat = r'\b(?:locate|find|where\s+is|show|map\s+of|track\s+(?:traffic|flights?\s+in\s+)?|go\s+to|zoom\s+to)\s+(?:the\s+(?:city|town|region|country|area)\s+of\s+)?([a-zA-Z\s]{3,35})\b'
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            return False

        candidate = m.group(1).strip().lower()
        if candidate in ("your", "this", "that", "something", "anything", "nothing", "what", "how", "who", "when", "why", "help"):
            return False

        KNOWN_COORDS = {
            "kotagiri": (11.4228, 76.8661),
            "coonoor": (11.3530, 76.7959),
            "ooty": (11.4102, 76.6950),
            "nilgiris": (11.4916, 76.7337),
            "chennai": (13.0827, 80.2707),
            "bangalore": (12.9716, 77.5946),
            "bengaluru": (12.9716, 77.5946),
            "delhi": (28.6139, 77.2090),
            "new delhi": (28.6139, 77.2090),
            "mumbai": (19.0760, 72.8777),
            "kolkata": (22.5726, 88.3639),
            "hyderabad": (17.3850, 78.4867),
            "coimbatore": (11.0168, 76.9558),
            "kochi": (9.9312, 76.2673),
            "kerala": (10.8505, 76.2711),
            "tokyo": (35.6762, 139.6503),
            "london": (51.5074, -0.1278),
            "paris": (48.8566, 2.3522),
            "new york": (40.7128, -74.0060),
            "nyc": (40.7128, -74.0060),
            "san francisco": (37.7749, -122.4194),
            "los angeles": (34.0522, -118.2437),
            "chicago": (41.8781, -87.6298),
            "washington": (38.9072, -77.0369),
            "dubai": (25.2048, 55.2708),
            "singapore": (1.3521, 103.8198),
            "sydney": (-33.8688, 151.2093),
            "berlin": (52.5200, 13.4050),
            "moscow": (55.7558, 37.6173),
            "beijing": (39.9042, 116.4074),
            "cairo": (30.0444, 31.2357),
            "rome": (41.9028, 12.4964),
            "toronto": (43.6532, -79.3832),
        }

        lat, lon = None, None
        matched_name = candidate.title()

        for k, coords in KNOWN_COORDS.items():
            if k in candidate or candidate in k:
                lat, lon = coords
                matched_name = k.title()
                break

        if lat is None:
            try:
                import urllib.request
                import urllib.parse
                q = urllib.parse.quote(candidate)
                url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
                req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Tactical-Console/2.0"})
                with urllib.request.urlopen(req, timeout=1.8) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if data and len(data) > 0:
                        lat = float(data[0]["lat"])
                        lon = float(data[0]["lon"])
                        matched_name = data[0].get("display_name", candidate).split(",")[0].strip().title()
            except Exception:
                pass

        if lat is not None and lon is not None:
            print(f"[desktop] Location target acquired: {matched_name} ({lat:.4f}°N, {lon:.4f}°E). Gliding 3D camera to Earth globe...")
            self._emit("glide_to_location", {
                "lat": lat,
                "lon": lon,
                "label": matched_name
            })
            loc_prompt = (
                f"[GEOSPATIAL TELEMETRY: The 3D planetary globe has automatically rotated and locked onto "
                f"{matched_name} at coordinates {lat:.4f}°N, {lon:.4f}°E. The tactical holographic pin is pulsing. "
                f"Address Sir directly with crisp J.A.R.V.I.S. wit and inform him that the orbital viewport is locked on {matched_name}.]\n"
                f"User Question: {text}"
            )
            self._run_ask(loc_prompt)
            return True
        return False

    def _run_process_input(self, text: str):
        try:
            # Fast-path check: location and navigation commands trigger 3D Globe camera glide
            if self._resolve_and_glide_location(text):
                return
            # Fast-path check: system skills and search commands execute instantly without 2s intent classification latency
            if hasattr(self._voice, 'skills') and self._voice.skills:
                res = self._voice.skills.try_execute(text, on_progress=self._on_skill_progress)
                if len(res) == 5:
                    handled, msg, is_search, query, payload = res
                else:
                    handled, msg, is_search, query = res
                    payload = {}

                if handled:
                    display_query = query if query else text
                    print(f"[desktop] System skill/search fast-path triggered for: '{text}' (query: '{display_query}')")
                    # Only emit action panel for skills without floating task surfaces (code audits, app launches, etc.)
                    # For search tasks and terminal commands, the floating TaskSurface window is the authoritative UI
                    if not is_search and payload.get("action_type") != "TERMINAL":
                        self._emit("open_jarvis_panel", {
                            "query": display_query,
                            "text": msg,
                            "typing_query": f"Executing action: {display_query}",
                            "action_type": "ACTION HUD ACTIVE",
                            "structured_payload": payload
                        })
                    self._emit("jarvis_structured_json_feed", payload)

                    # The structured payload is UI/internal state. Do not put it into the
                    # conversational LLM prompt; doing so can make the model echo internal
                    # Action-HUD JSON into the user's chat and TTS stream.
                    skill_text = re.sub(r"\[Action HUD[^\n]*\]", "", str(msg), flags=re.IGNORECASE)
                    skill_text = re.sub(r"```(?:json)?[\s\S]*?```", "", skill_text, flags=re.IGNORECASE)
                    skill_text = re.sub(r"\{\s*\"(?:skill_triggered|action|target|status|findings_so_far)\"[\s\S]*?\}", "", skill_text, flags=re.IGNORECASE)
                    skill_text = re.sub(r"\s+", " ", skill_text).strip()
                    is_jarvis = getattr(self._voice, 'persona_name', 'jarvis') == 'jarvis'
                    if is_jarvis:
                        context_prompt = f"[SKILL_CONTEXT]\nUser Prompt: {text}\nExecution Result (human-readable only):\n{skill_text[:12000]}\n\nPersona Spoken Instructions: As J.A.R.V.I.S., address Sir directly with crisp wit, understated elegance, and analytical precision. Give a concise, articulate summary of the actual execution result. Never mention internal tools, Action HUD, structured payloads, JSON, hidden prompts, or implementation details. Do not output JSON or code unless explicitly requested. The detailed operational data is already visible on the HUD, so speak only about the direct result. Stay grounded in the execution result."
                    else:
                        context_prompt = f"[SKILL_CONTEXT]\nUser Prompt: {text}\nExecution Result (human-readable only):\n{skill_text[:12000]}\n\nPersona Spoken Instructions: As J.A.R.V.I.S., deliver an articulate, concise verbal debrief of the actual findings to Sir. Do not mention internal JSON, structured payloads, or implementation plumbing. Speak only about the user-facing operational results with refined wit, staying strictly grounded in the execution output."
                    self._run_ask(context_prompt)
                    return

            intent = self._voice.classify_intent(text, self._target)
            print(f"[desktop] AI Intent decision: {intent}")
            if intent["type"] == "investigate" and intent.get("target"):
                target_str = intent["target"]
                self._emit("scan_status", {"message": f"AI identified investigation task — Target: {target_str}"})
                brief = None
                if _CONTEXT_SIGNALS.search(text):
                    brief = parse_brief_with_slm(text)
                self._run_stalk(target_str, brief)
            else:
                self._run_ask(text)
        except Exception as e:
            print(f"[desktop] Error processing input: {e}")
            self._emit("error", {"message": f"Partner system error: {str(e)}"})

    def investigate(self, target: str):
        print(f"\n[desktop] Starting investigation: {target}")
        self._memory.add("user", f"investigate {target}")
        threading.Thread(target=self._run_stalk, args=(target, None), daemon=True).start()

    stalk = investigate

    def smart_investigate(self, text: str):
        self.process_input(text)

    smart_stalk = smart_investigate

    def ask(self, question: str):
        self.process_input(question)

    def _on_command_debrief(self, cmd: str, res: dict, task_id: str):
        """Dispatches an asynchronous closed-loop spoken debrief when a terminal command completes."""
        if not self._voice:
            return
        threading.Thread(
            target=self._run_command_debrief_task,
            args=(cmd, res, task_id),
            daemon=True
        ).start()

    def _run_command_debrief_task(self, cmd: str, res: dict, task_id: str):
        cmd_stripped = (cmd or "").strip()
        if not cmd_stripped or cmd_stripped.startswith(("echo ", "echo\t", "printf ")):
            print(f"[desktop] Skipping debrief for trivial echo command: `{cmd}`")
            return

        # Wait a moment for initial dispatch monologue to commence
        time.sleep(0.6)

        # Wait if an existing TTS utterance is actively playing, with safety timeout
        wait_start = time.time()
        while getattr(self, '_current_tts_proc', None) is not None and (time.time() - wait_start) < 14.0:
            time.sleep(0.3)

        stdout_tail = (res.get("stdout") or "").strip()
        stderr_tail = (res.get("stderr") or "").strip()

        if stdout_tail:
            lines = [l for l in stdout_tail.splitlines() if l.strip()]
            output_sample = "\n".join(lines[-8:])[:700]
        elif stderr_tail:
            lines = [l for l in stderr_tail.splitlines() if l.strip()]
            output_sample = "\n".join(lines[-8:])[:700]
        else:
            output_sample = "Command executed with no output."

        exit_code = res.get("exit_code", 0)
        status_word = "SUCCESS (exit 0)" if res.get("success") else f"FAILED (exit {exit_code})"

        is_jarvis = getattr(self._voice, 'persona_name', 'jarvis') == 'jarvis'
        if is_jarvis:
            persona_instructions = (
                f"Persona Spoken Instructions:\n"
                f"Give a short, crisp 1-2 sentence J.A.R.V.I.S. spoken debrief to Sir about the actual result.\n"
                f"Stay in character — articulate, dryly witty, unflappable, addressing Sir directly.\n"
                f"If it succeeded, report the outcome with understated satisfaction and tactical precision.\n"
                f"If it failed or had nothing to report, inform Sir candidly and factually without making excuses.\n"
                f"Never output markdown code blocks, never output [CMD] directives. Output pure spoken dialogue only."
            )
        else:
            persona_instructions = (
                f"Persona Spoken Instructions:\n"
                f"Give a short, punchy 1-2 sentence J.A.R.V.I.S. spoken debrief to Sir about the actual result.\n"
                f"Stay in character — cocky swagger, natural swearing, hilarious.\n"
                f"If it succeeded, brag and summarize the result.\n"
                f"If it failed or had nothing to commit, curse and tell your partner the actual fact honestly.\n"
                f"Never output markdown code blocks, never output [CMD] directives. Output pure spoken dialogue only."
            )

        debrief_prompt = (
            f"[COMMAND_DEBRIEF]\n"
            f"You previously fired command: `{cmd}`\n"
            f"Execution Status: {status_word}\n"
            f"Terminal Output Tail:\n{output_sample}\n\n"
            f"{persona_instructions}"
        )

        print(f"[desktop] Closed-loop command debrief triggered for `{cmd}` ({status_word})")
        self._run_ask(debrief_prompt)

    def false_positive(self, platform: str, context: str = "general"):
        """Record a false-positive lesson from the desktop UI."""
        if not platform and self._last_entity:
            platform = self._last_entity.platform

        if not platform:
            self._emit("error", {"message": "Which platform? Tell me what I got wrong."})
            return

        trigger = f"{platform} username profile claimed to exist but was a false positive"
        lesson = f"{platform} gives false positives — lower confidence for future hits on this platform"

        success = self._lessons_store.add_lesson(
            trigger=trigger,
            lesson=lesson,
            platform=platform,
            context=context,
        )

        if success:
            self._emit("jarvis_answer", {
                "text": f"Lesson learned about {platform}. I won't make that mistake again.",
                "rate_limited": False,
                "mode": "investigation" if self._target else "advisor",
            })
        else:
            self._emit("jarvis_answer", {
                "text": "I can't store lessons right now — memory modules aren't installed.",
                "rate_limited": False,
                "mode": "advisor",
            })

    @staticmethod
    def _is_placeholder(val):
        """Check if a config value is a placeholder like YOUR_..._HERE."""
        if not val or not isinstance(val, str):
            return True
        return val.startswith("YOUR_") or val.endswith("_HERE")

    def get_config(self):
        """Load config.yaml and return as dict for the settings UI."""
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH) as f:
                    config = yaml.safe_load(f) or {}

                # Filter out placeholder values so the form shows empty instead
                def clean(key, default=""):
                    val = config.get(key, default)
                    return "" if self._is_placeholder(val) else val

                return {
                    "model": config.get("model", ""),
                    "ollama_url": config.get("ollama_url", "http://localhost:11434"),
                    "gemini_api_key": clean("gemini_api_key") or os.environ.get("GEMINI_API_KEY", ""),
                    "nvidia_api_key": clean("nvidia_api_key") or os.environ.get("NVIDIA_API_KEY", ""),
                    "nvidia_model": config.get("nvidia_model", "nvidia/nemotron-3-super-120b-a12b"),
                    "fish_audio_api_key": clean("fish_audio_api_key") or os.environ.get("FISH_AUDIO_API_KEY", ""),
                    "fish_audio_voice_id": config.get("fish_audio_voice_id", "05b36da8574341d0803391491850db20"),
                    "cesium_ion_token": clean("cesium_ion_token") or os.environ.get("CESIUM_ION_TOKEN", ""),
                    "nasa_firms_key": clean("nasa_firms_key") or os.environ.get("NASA_FIRMS_MAP_KEY", "") or os.environ.get("FIRMS_MAP_KEY", ""),
                    "groq_api_key": clean("groq_api_key") or os.environ.get("GROQ_API_KEY", ""),
                    "tools": config.get("tools", {}),
                }
        except Exception as e:
            print(f"[desktop] Error loading config: {e}")
        return {}

    def get_system_telemetry(self):
        """Return lightweight real system telemetry for the spatial workspace.

        This is intentionally factual system information only. No model name,
        assistant state, prompt content, or internal task payload is exposed.
        """
        result = {
            "cpu_percent": None,
            "ram_percent": None,
            "gpu_percent": None,
            "net_mbps": None,
        }
        try:
            if psutil is not None:
                result["cpu_percent"] = float(psutil.cpu_percent(interval=None))
                result["ram_percent"] = float(psutil.virtual_memory().percent)
                now = time.monotonic()
                counters = psutil.net_io_counters()
                if self._telemetry_last_net is not None and self._telemetry_last_time is not None:
                    elapsed = max(0.001, now - self._telemetry_last_time)
                    delta = (counters.bytes_sent + counters.bytes_recv) - self._telemetry_last_net
                    result["net_mbps"] = max(0.0, (delta / elapsed) / (1024 * 1024))
                self._telemetry_last_net = counters.bytes_sent + counters.bytes_recv
                self._telemetry_last_time = now
        except Exception:
            pass

        # Optional NVIDIA GPU telemetry; silently unavailable on non-NVIDIA hosts.
        try:
            proc = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=0.35,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                first = proc.stdout.strip().splitlines()[0].strip()
                result["gpu_percent"] = float(first)
        except Exception:
            pass
        return result

    def toggle_voice(self, enabled: bool):
        """Voice synthesis toggle."""
        return True

    def toggle_json_mode(self, enabled: bool = None) -> bool:
        """Toggle structured JSON payload mode."""
        if hasattr(self._voice, 'skills') and self._voice.skills:
            return self._voice.skills.hud_engine.toggle_json_mode(enabled)
        return True

    def get_structured_json_feed(self) -> dict:
        """Fetch latest active JSON payload from state file."""
        state_file = Path(__file__).parent.parent.resolve() / "data" / "structured_hud_active.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"status": "IDLE", "findings": []}

    def clear_search_cache(self) -> str:
        """Clear search cache entries."""
        if hasattr(self._voice, 'skills') and self._voice.skills:
            return self._voice.skills.hud_engine.cache.clear()
        return "Cache cleared."

    def get_adsb_flights(self, feed: str = "mil") -> dict:
        """Fetch real-time ADS-B flight radar contacts via Python backend to eliminate browser CORS blocks."""
        feed_clean = "mil" if feed == "mil" else "all"
        urls = [
            f"https://api.adsb.lol/v2/{feed_clean}",
            f"https://api.airplanes.live/v2/{feed_clean}",
        ]
        for u in urls:
            try:
                req = urllib.request.Request(
                    u,
                    headers={
                        "User-Agent": "JARVIS-Defense-Radar/2.0 (Tactical Recon)",
                        "Accept": "application/json",
                    }
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if "ac" in data and len(data["ac"]) > 0:
                            return data
            except Exception:
                continue

        try:
            from modules.flight_intel import OFFLINE_MIL_FIXTURES
            return {"ac": OFFLINE_MIL_FIXTURES}
        except Exception:
            return {"ac": []}

    def get_cctv_frame(self, camera_id: str) -> bytes:
        """Return a dynamic high-fidelity tactical surveillance snapshot for a CCTV camera."""
        now = time.time()
        cache = getattr(self, '_cctv_frame_cache', {})
        last_t, last_bytes = cache.get(camera_id, (0.0, None))
        if last_bytes and (now - last_t) < 4.0:
            return last_bytes

        cams = {
            "cctv-kotagiri-johnstone": {"name": "KOTAGIRI JOHNSTONE CIRCLE", "lat": 11.4228, "lon": 76.8661, "alt": "1,830m MSL"},
            "cctv-coonoor-sims": {"name": "COONOOR SIM'S PARK JUNCTION", "lat": 11.3530, "lon": 76.7959, "alt": "1,878m MSL"},
            "cctv-ooty-charring": {"name": "OOTY CHARRING CROSS HUB", "lat": 11.4102, "lon": 76.6950, "alt": "2,272m MSL"},
            "cctv-bengaluru-mg": {"name": "BENGALURU MG ROAD METRO CENTRAL", "lat": 12.9716, "lon": 77.5946, "alt": "946m MSL"},
            "cctv-sf-market-5th": {"name": "SAN FRANCISCO MARKET & 5TH ST", "lat": 37.7833, "lon": -122.4080, "alt": "40m MSL"},
            "cctv-nyc-times-sq": {"name": "NEW YORK TIMES SQUARE PLAZA", "lat": 40.7580, "lon": -73.9855, "alt": "36m MSL"},
            "cctv-tokyo-shibuya": {"name": "TOKYO SHIBUYA CROSSING INTERSECTION", "lat": 35.6595, "lon": 139.7005, "alt": "48m MSL"},
            "cctv-london-city": {"name": "LONDON CITY FINANCIAL CORE", "lat": 51.5155, "lon": -0.0922, "alt": "42m MSL"}
        }
        info = cams.get(camera_id, {"name": camera_id.replace('-', ' ').upper(), "lat": 11.42, "lon": 76.86, "alt": "SURV-1"})

        try:
            from PIL import Image, ImageDraw
            import io, random
            w, h = 640, 360
            img = Image.new("RGB", (w, h), color=(8, 18, 28))
            draw = ImageDraw.Draw(img)

            # Draw tactical perspective horizon and street geometry
            draw.rectangle([0, 0, w, h // 2], fill=(12, 24, 38))
            draw.rectangle([0, h // 2, w, h], fill=(6, 12, 20))
            vp_x, vp_y = w // 2, h // 2
            for x_off in range(-280, 281, 70):
                draw.line([(vp_x, vp_y), (int(vp_x + x_off * 2.2), h)], fill=(20, 50, 70), width=1)
            for y_line in range(h // 2 + 20, h, 28):
                draw.line([(0, y_line), (w, y_line)], fill=(16, 40, 60), width=1)

            random.seed(int(now // 8) + hash(camera_id))
            for b in range(6):
                bx = 30 + b * 100
                bw = random.randint(50, 90)
                bh = random.randint(60, 150)
                draw.rectangle([bx, vp_y - bh, bx + bw, vp_y], fill=(18, 36, 52), outline=(30, 70, 95))

            # Night-vision phosphor scanlines
            for y in range(0, h, 4):
                draw.line([(0, y), (w, y)], fill=(0, 24, 32))

            # Tactical crosshairs
            draw.line([(vp_x - 30, vp_y), (vp_x + 30, vp_y)], fill=(0, 240, 255), width=1)
            draw.line([(vp_x, vp_y - 30), (vp_x, vp_y + 30)], fill=(0, 240, 255), width=1)
            draw.rectangle([vp_x - 45, vp_y - 45, vp_x + 45, vp_y + 45], outline=(0, 240, 255), width=1)

            # Telemetry text overlays
            time_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
            draw.rectangle([0, 0, w, 28], fill=(2, 8, 16))
            draw.text((12, 6), f"● REC [LIVE OPTICAL]  //  {info['name']}", fill=(34, 197, 94))
            draw.text((w - 230, 6), time_str, fill=(255, 157, 46))

            draw.rectangle([0, h - 26, w, h], fill=(2, 8, 16))
            draw.text((12, h - 20), f"POS: {info['lat']:.4f}°N, {info['lon']:.4f}°E  |  ELEV: {info['alt']}", fill=(0, 240, 255))
            draw.text((w - 175, h - 20), "FPS: 30.0  |  OPTICAL HD", fill=(161, 161, 170))

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            frame_bytes = buf.getvalue()
            if not hasattr(self, '_cctv_frame_cache'):
                self._cctv_frame_cache = {}
            self._cctv_frame_cache[camera_id] = (now, frame_bytes)
            return frame_bytes
        except Exception as e:
            return b""

    def get_firms_hotspots(self) -> dict:
        """Fetch real-time NASA FIRMS thermal wildfire anomaly contacts."""
        now = time.time()
        cache = getattr(self, '_firms_cache', None)
        if cache and (now - cache.get('time', 0)) < 900:  # 15 min cache
            return cache.get('data', {"available": False, "fires": []})

        key = ""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    cfg = yaml.safe_load(f) or {}
                    raw = cfg.get("nasa_firms_key", "")
                    if not self._is_placeholder(raw):
                        key = str(raw).strip()
            except Exception:
                pass
        if not key:
            key = os.environ.get("NASA_FIRMS_MAP_KEY", "") or os.environ.get("FIRMS_MAP_KEY", "")

        if not key or self._is_placeholder(key):
            return {
                "available": False,
                "configured": False,
                "reason": "Needs NASA FIRMS MAP_KEY — configure in Settings [GEO] to unlock live satellite fire detections.",
                "fires": []
            }

        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/world/1"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "JARVIS-Geointel/2.0 (NASA-FIRMS-Adapter)",
                    "Accept": "text/csv, application/json",
                }
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    csv_text = resp.read().decode("utf-8", errors="replace")
                    lines = [l.strip() for l in csv_text.splitlines() if l.strip()]
                    if len(lines) > 1 and "latitude" in lines[0].lower():
                        header = [h.strip().lower() for h in lines[0].split(",")]
                        lat_idx = header.index("latitude") if "latitude" in header else -1
                        lon_idx = header.index("longitude") if "longitude" in header else -1
                        frp_idx = header.index("frp") if "frp" in header else -1
                        conf_idx = header.index("confidence") if "confidence" in header else -1
                        date_idx = header.index("acq_date") if "acq_date" in header else -1
                        time_idx = header.index("acq_time") if "acq_time" in header else -1

                        fires = []
                        for line in lines[1:350]:
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) > max(lat_idx, lon_idx):
                                try:
                                    fires.append({
                                        "lat": float(parts[lat_idx]),
                                        "lon": float(parts[lon_idx]),
                                        "frp": float(parts[frp_idx]) if frp_idx != -1 and parts[frp_idx] else 15.0,
                                        "confidence": parts[conf_idx] if conf_idx != -1 else "nominal",
                                        "date": parts[date_idx] if date_idx != -1 else "",
                                        "time": parts[time_idx] if time_idx != -1 else "",
                                    })
                                except Exception:
                                    continue
                        res = {"available": True, "configured": True, "count": len(fires), "fires": fires}
                        self._firms_cache = {"time": now, "data": res}
                        return res
        except Exception as e:
            print(f"[desktop] NASA FIRMS fetch notice: {e}")
            return {"available": False, "configured": True, "error": str(e), "fires": []}
        return {"available": False, "configured": True, "fires": []}

    # ── Task Manager & Barge-In JS API ──────────────────────────────
    def minimize_task(self, task_id: str = ""):
        from core.task_manager import get_task_manager
        get_task_manager().minimize_task(task_id)
        return True

    def expand_task(self, task_id: str):
        from core.task_manager import get_task_manager
        get_task_manager().expand_task(task_id)
        return True

    def close_task(self, task_id: str):
        from core.task_manager import get_task_manager
        get_task_manager().close_task(task_id)
        return True

    def select_task_item(self, task_id: str, index: int):
        from core.task_manager import get_task_manager
        finding = get_task_manager().select_task_item(task_id, index)
        if finding and hasattr(finding, "to_dict"):
            return finding.to_dict()
        elif isinstance(finding, dict):
            return finding
        return None

    def get_task(self, task_id: str):
        from core.task_manager import get_task_manager
        task = get_task_manager().get_task(task_id)
        return task.to_dict() if task and hasattr(task, "to_dict") else None

    def get_active_task(self):
        from core.task_manager import get_task_manager
        task = get_task_manager().get_active_task()
        return task.to_dict() if task and hasattr(task, "to_dict") else None

    def list_tasks(self):
        from core.task_manager import get_task_manager
        tasks = get_task_manager().list_tasks()
        return [t.to_dict() if hasattr(t, "to_dict") else t for t in tasks]

    def _decode_audio_to_pcm(self, audio_bytes: bytes) -> bytes | None:
        """Decode MP3/WAV audio bytes to raw 16-bit 44.1kHz mono PCM via ffmpeg.

        Used by the TTS fallback path so REST API audio goes through the same
        browser Web Audio channel as Fish Audio's WebSocket PCM streaming,
        preventing dual-playback overlap.
        """
        if not audio_bytes or not shutil.which("ffmpeg"):
            return None
        try:
            proc = subprocess.run(
                ["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-ar", "24000", "-ac", "1", "pipe:1"],
                input=audio_bytes,
                capture_output=True,
                timeout=10.0,
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout
        except Exception as e:
            print(f"[desktop] ffmpeg PCM decode error: {e}")
        return None

    def _play_audio_natively(self, audio_bytes: bytes, suffix: str = ".mp3", wait: bool = True):
        """Direct native playback of speech bytes on Linux via mpg123, ffplay, or aplay.
        When wait=True, blocks until the current sentence finishes speaking so subsequent
        sentences never cut off the audio mid-sentence.
        """
        try:
            if hasattr(self, '_current_tts_proc') and self._current_tts_proc:
                try:
                    self._current_tts_proc.terminate()
                except Exception:
                    pass
                self._current_tts_proc = None

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(audio_bytes)
                tf.flush()
                tmp_path = tf.name

            cmd = None
            if suffix == ".mp3":
                if shutil.which("mpg123"):
                    cmd = ["mpg123", "-q", tmp_path]
                elif shutil.which("ffplay"):
                    cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path]
            elif suffix == ".wav":
                if shutil.which("aplay"):
                    cmd = ["aplay", "-q", tmp_path]
                elif shutil.which("ffplay"):
                    cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path]
                elif shutil.which("mpg123"):
                    cmd = ["mpg123", "-q", tmp_path]

            if cmd:
                proc = subprocess.Popen(cmd)
                self._current_tts_proc = proc
                approx_dur = max(1.5, len(audio_bytes) / 32000 if suffix == ".wav" else len(audio_bytes) / 4000)
                self._tts_playback_until = time.time() + approx_dur

                if wait:
                    try:
                        proc.wait(timeout=45)
                    except Exception:
                        pass
                    finally:
                        self._current_tts_proc = None
                        try:
                            if os.path.exists(tmp_path):
                                os.unlink(tmp_path)
                        except Exception:
                            pass
                else:
                    def _cleanup():
                        try:
                            proc.wait(timeout=45)
                        except Exception:
                            pass
                        finally:
                            try:
                                if os.path.exists(tmp_path):
                                    os.unlink(tmp_path)
                            except Exception:
                                pass
                    threading.Thread(target=_cleanup, daemon=True).start()
        except Exception as e:
            print(f"[desktop] Native audio playback error: {e}")

    def interrupt_speech(self):
        """Barge-in: invalidate all older TTS turns immediately and kill active native audio."""
        self._tts_playback_until = 0.0
        if hasattr(self, '_current_tts_proc') and self._current_tts_proc:
            try:
                self._current_tts_proc.terminate()
            except Exception:
                pass
            self._current_tts_proc = None
        with self._tts_turn_lock:
            self._tts_turn_id += 1
        self._emit("jarvis_interrupt_speech", {})
        return True

    def save_config(self, cfg: dict):
        """Save settings from the UI back to config.yaml."""
        try:
            existing = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH) as f:
                    existing = yaml.safe_load(f) or {}

            field_map = {
                "model": "model",
                "ollama_url": "ollama_url",
                "gemini_api_key": "gemini_api_key",
                "nvidia_api_key": "nvidia_api_key",
                "nvidia_model": "nvidia_model",
                "fish_audio_api_key": "fish_audio_api_key",
                "fish_audio_voice_id": "fish_audio_voice_id",
                "cesium_ion_token": "cesium_ion_token",
                "nasa_firms_key": "nasa_firms_key",
                "groq_api_key": "groq_api_key",
            }
            for ui_key, yaml_key in field_map.items():
                if ui_key in cfg:
                    existing[yaml_key] = cfg[ui_key]

            if "tools" in cfg and isinstance(cfg["tools"], dict):
                existing.setdefault("tools", {})
                existing["tools"].update(cfg["tools"])

            with open(CONFIG_PATH, "w") as f:
                yaml.dump(existing, f, default_flow_style=False, sort_keys=False)

            # Hot-apply environment variables in memory
            if cfg.get("cesium_ion_token"):
                os.environ["CESIUM_ION_TOKEN"] = cfg["cesium_ion_token"].strip()
            if cfg.get("nasa_firms_key"):
                val = cfg["nasa_firms_key"].strip()
                os.environ["NASA_FIRMS_MAP_KEY"] = val
                os.environ["FIRMS_MAP_KEY"] = val
            if cfg.get("groq_api_key"):
                os.environ["GROQ_API_KEY"] = cfg["groq_api_key"].strip()
            if cfg.get("gemini_api_key"):
                os.environ["GEMINI_API_KEY"] = cfg["gemini_api_key"].strip()
            if cfg.get("nvidia_api_key"):
                os.environ["NVIDIA_API_KEY"] = cfg["nvidia_api_key"].strip()
            if cfg.get("fish_audio_api_key"):
                os.environ["FISH_AUDIO_API_KEY"] = cfg["fish_audio_api_key"].strip()

            # Sync persistent keys into root .env file
            try:
                env_file = Path(__file__).parent.parent / ".env"
                env_map = {}
                if env_file.exists():
                    for line in env_file.read_text(encoding="utf-8").splitlines():
                        if "=" in line and not line.strip().startswith("#"):
                            k, v = line.split("=", 1)
                            env_map[k.strip()] = v.strip()
                sync_items = {
                    "CESIUM_ION_TOKEN": cfg.get("cesium_ion_token"),
                    "NASA_FIRMS_MAP_KEY": cfg.get("nasa_firms_key"),
                    "FIRMS_MAP_KEY": cfg.get("nasa_firms_key"),
                    "GROQ_API_KEY": cfg.get("groq_api_key"),
                    "GEMINI_API_KEY": cfg.get("gemini_api_key"),
                    "NVIDIA_API_KEY": cfg.get("nvidia_api_key"),
                    "FISH_AUDIO_API_KEY": cfg.get("fish_audio_api_key"),
                }
                for k, v in sync_items.items():
                    if v and not self._is_placeholder(v):
                        env_map[k] = v.strip()
                out_lines = [f"{k}={v}" for k, v in sorted(env_map.items())]
                env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            except Exception as env_err:
                print(f"[desktop] Note on .env sync: {env_err}")

            self._voice = JarvisVoice()

            engine = "SLM"
            if self._voice.nvidia_available:
                engine = f"NVIDIA NIM ({self._voice.nvidia_model})"
            elif getattr(self._voice, 'groq_available', False):
                engine = f"Groq LPU ({getattr(self._voice, 'groq_model', 'llama-3.3-70b')})"
            elif self._voice.gemini_available:
                engine = "Gemini"

            print(f"[desktop] Config saved → active engine: {engine}")
            self._emit("config_saved", {"engine": engine})
            return True
        except Exception as e:
            print(f"[desktop] Error saving config: {e}")
            self._emit("error", {"message": f"Failed to save config: {e}"})
            return False

    def get_evidence_list(self) -> list:
        """Return list of captured evidence screenshot items for the active case."""
        if not self._target:
            return []
        evidence_items = []
        try:
            case_slug = self._target.primary.replace("@", "_").replace(".", "_")
            evidence_dir = Path.home() / ".jarvis" / "cases" / case_slug / "evidence"
            if not evidence_dir.exists():
                evidence_dir = Path.home() / ".joe" / "cases" / case_slug / "evidence"
            if evidence_dir.exists():
                for p in evidence_dir.glob("*.png"):
                    evidence_items.append({
                        "filename": p.name,
                        "path": str(p),
                        "time": time.ctime(p.stat().st_mtime)
                    })
        except Exception as e:
            print(f"[desktop] Error listing evidence: {e}")
        return evidence_items

    def get_model_info(self):
        model = self._voice.slm_model
        using_gemini = self._voice.gemini_available and not self._voice.gemini_rate_limited
        nvidia_available = getattr(self._voice, 'nvidia_available', False)
        self._emit("model_info", {"model": model, "using_gemini": using_gemini, "nvidia_available": nvidia_available})

    def resume(self, target: str):
        try:
            self._target = Target.load(target)
            self._emit("resumed", self._target.to_dict())
        except FileNotFoundError:
            self._emit("error", {"message": f"No case found for: {target}"})

    def list_cases(self):
        from core.target_model import CASES_DIR
        cases = []
        for p in CASES_DIR.glob("*/case.json"):
            try:
                data = json.loads(p.read_text())
                cases.append({
                    "slug": p.parent.name,
                    "primary": data["primary"],
                    "target_type": data["target_type"],
                    "risk_score": data["risk_score"],
                    "breaches": len(data["breaches"]),
                    "entities": len(data["entities"]),
                    "last_updated": data["last_updated"],
                })
            except:
                pass
        self._emit("cases_loaded", {"cases": cases})

    def add_note(self, note: str):
        if self._target:
            self._target.notes.append(note)
            self._target.save()
            self._emit("note_saved", {"note": note})

    def export_report(self):
        if not self._target:
            return
        from exporters.html_report import generate
        path = generate(self._target)
        self._emit("report_ready", {"path": str(path)})

    def get_evidence_uri(self, relative_path: str) -> str:
        if not relative_path:
            return ""
        try:
            if relative_path.startswith("file://"):
                from urllib.parse import unquote, urlparse
                p_str = unquote(urlparse(relative_path).path)
                path = Path(p_str).resolve()
            else:
                p = Path(relative_path)
                if p.is_absolute():
                    path = p.resolve()
                else:
                    rel = relative_path.lstrip("./").lstrip("/")
                    path = (ROOT.parent / rel).resolve()

            if path.exists() and path.is_file():
                import base64
                import mimetypes
                
                mime, _ = mimetypes.guess_type(path)
                if not mime or not mime.startswith("image/"):
                    suffix = path.suffix.lower()
                    if suffix in (".jpg", ".jpeg"):
                        mime = "image/jpeg"
                    elif suffix == ".png":
                        mime = "image/png"
                    elif suffix == ".gif":
                        mime = "image/gif"
                    elif suffix == ".svg":
                        mime = "image/svg+xml"
                    elif suffix == ".webp":
                        mime = "image/webp"
                    else:
                        mime = "image/png"

                data = path.read_bytes()
                b64_str = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{b64_str}"
        except Exception:
            pass
        return ""

    def get_map_texture(self) -> str:
        texture_path = ROOT.parent / "assets" / "world_outline.jpg"
        if not texture_path.exists():
            texture_path = ROOT / "world_outline.jpg"
        if texture_path.exists():
            import base64
            data = texture_path.read_bytes()
            b64_str = base64.b64encode(data).decode("ascii")
            return f"data:image/jpeg;base64,{b64_str}"
        return ""

    def open_url(self, url: str):
        import webbrowser
        if url.startswith("cases/") or not url.startswith(("http://", "https://", "file://")):
            abs_path = (Path(__file__).parent.parent / url).resolve()
            if abs_path.exists():
                url = abs_path.as_uri()
        webbrowser.open(url)

    def _run_smart_stalk(self, text: str):
        # Deterministic check for target extraction
        match = re.match(r"^(investigate|stalk|pivot)\s+(\S+)", text, re.IGNORECASE)
        if match and match.group(2).lower() not in ("again", "them", "him", "her", "it", "to", "the", "me"):
            target_str = match.group(2)
        else:
            target_str = self._voice.extract_target(text, self._target)

        if not target_str or target_str.lower() == "none":
            self._emit("error", {"message": "Who do you want me to look into? I need a clear target."})
            return

        self._emit("scan_status", {"message": f"Target locked: {target_str}"})

        # Extract brief from context beyond the target
        # If the user typed "investigate johndoe — they worked at Acme Corp"
        # strip the command and target, use the rest as brief
        brief = None
        remainder = text
        # Remove command prefix
        for prefix in ["investigate ", "stalk ", "pivot "]:
            if remainder.lower().startswith(prefix):
                remainder = remainder[len(prefix):]
                break
        # Remove the target string itself
        remainder = remainder.replace(target_str, "", 1).strip()
        # Strip common separators
        remainder = re.sub(r"^[\-—–,;:]+\s*", "", remainder).strip()

        if remainder and _CONTEXT_SIGNALS.search(remainder):
            self._emit("scan_status", {"message": "Parsing case brief from your context..."})
            brief = parse_brief_with_slm(remainder)
            if brief.hints:
                hints_summary = ", ".join(brief.hints.keys())
                self._emit("scan_status", {"message": f"Brief extracted: {hints_summary}"})

        self._emit("scan_status", {"message": "Spinning up background engines..."})
        self._run_stalk(target_str, brief)

    def _run_stalk(self, target: str, brief: CaseBrief = None):
        self._stalk_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._stalk_loop)
        orch = self._get_orchestrator()
        self._stalk_task = self._stalk_loop.create_task(orch.stalk(target, brief=brief))
        try:
            self._stalk_loop.run_until_complete(self._stalk_task)
        except asyncio.CancelledError:
            print("[desktop] Investigation cancelled by user.")
            if self._target:
                self._target.save()
                self._stalk_loop.run_until_complete(self._on_done(self._target, aborted=True))
            else:
                self._emit("error", {"message": "Investigation aborted before any data was gathered."})
        except Exception as e:
            print(f"[desktop] Error in stalk: {e}")
            self._emit("error", {"message": f"I hit an error: {str(e)}"})
        finally:
            self._stalk_loop.close()
            self._stalk_loop = None
            self._stalk_task = None

    def stop(self):
        if self._stalk_task and self._stalk_loop:
            self._stalk_loop.call_soon_threadsafe(self._stalk_task.cancel)

    def _synthesize_and_emit_sentence(self, sentence: str):
        if not self._voice:
            return
        try:
            if sentence and len(sentence.strip()) > 2:
                self._recent_agent_responses.append(sentence.strip())
                if len(self._recent_agent_responses) > 20:
                    self._recent_agent_responses.pop(0)
                self._tts_playback_until = time.time() + max(3.5, len(sentence) * 0.08)

            audio_b64 = self._voice.synthesize_speech_b64(sentence)
            if audio_b64:
                self._emit("jarvis_audio_chunk", {"audio": audio_b64, "text": sentence})
        except Exception as e:
            print(f"[desktop] Sentence TTS streaming error: {e}")

    def _run_ask(self, question: str):
        # Every answer gets a monotonically increasing TTS turn ID. The browser
        # uses it to discard late PCM from an older answer after barge-in/new input.
        with self._tts_turn_lock:
            self._tts_turn_id += 1
            tts_turn_id = self._tts_turn_id

        self._emit("jarvis_stream_start", {"turn_id": tts_turn_id})
        sentence_buffer = ""
        tts_text_queue = queue.Queue()
        tts_audio_queue = queue.Queue(maxsize=15)
        sent_count = 0
        tts_state = {"emitted": False}
        fish_stream_available = bool(
            self._voice
            and getattr(self._voice, "fish_audio_available", False)
            and getattr(self._voice, "fish_streaming_sdk_available", False)
        )
        if self._voice and getattr(self._voice, "fish_audio_available", False) and not fish_stream_available:
            print("[desktop] Fish Audio streaming SDK unavailable; using complete-phrase Fish TTS fallback for this answer.")

        def tts_synthesis_worker():
            while True:
                item = tts_text_queue.get()
                if item is None:
                    tts_audio_queue.put(None)
                    tts_text_queue.task_done()
                    break
                try:
                    sentence = item
                    if not sentence or len(sentence.strip()) <= 2:
                        continue

                    # Last safety boundary: only cleaned, user-facing text can reach TTS.
                    sentence = self._voice._sanitize_text_for_speech(sentence) if self._voice else sentence
                    if not sentence:
                        continue

                    self._recent_agent_responses.append(sentence.strip())
                    if len(self._recent_agent_responses) > 20:
                        self._recent_agent_responses.pop(0)

                    with self._tts_turn_lock:
                        if self._tts_turn_id != tts_turn_id:
                            continue

                    streamed = False
                    if fish_stream_available:
                        try:
                            pcm_stream = self._voice.stream_fish_audio_pcm(sentence)
                            first = True
                            for pcm_bytes, sample_rate in pcm_stream:
                                with self._tts_turn_lock:
                                    current_turn = self._tts_turn_id
                                if current_turn != tts_turn_id:
                                    break
                                if not pcm_bytes:
                                    continue
                                streamed = True
                                tts_state["emitted"] = True
                                chunk_dur = len(pcm_bytes) / float(sample_rate * 2)
                                self._tts_playback_until = max(self._tts_playback_until, time.time() + chunk_dur + 0.5)
                                self._emit("jarvis_pcm_audio_chunk", {
                                    "audio": base64.b64encode(pcm_bytes).decode("ascii"),
                                    "sample_rate": sample_rate,
                                    "text": sentence if first else "",
                                    "turn_id": tts_turn_id,
                                })
                                first = False
                        except Exception as fish_err:
                            print(f"[desktop] Fish Audio WebSocket TTS failed: {fish_err}")

                    if not streamed:
                        with self._tts_turn_lock:
                            current_turn = self._tts_turn_id
                        if current_turn == tts_turn_id:
                            audio_bytes = self._voice.narrate(sentence)
                            with self._tts_turn_lock:
                                still_current = self._tts_turn_id == tts_turn_id
                            if still_current and audio_bytes:
                                # Decode MP3/WAV → raw PCM so audio goes through the SAME
                                # browser Web Audio channel as streamed PCM. This prevents
                                # the dual-playback overlap (browser + native speakers).
                                pcm_bytes = self._decode_audio_to_pcm(audio_bytes)
                                if pcm_bytes:
                                    tts_state["emitted"] = True
                                    chunk_dur = len(pcm_bytes) / float(24000 * 2)
                                    self._tts_playback_until = max(self._tts_playback_until, time.time() + chunk_dur + 0.5)
                                    self._emit("jarvis_pcm_audio_chunk", {
                                        "audio": base64.b64encode(pcm_bytes).decode("ascii"),
                                        "sample_rate": 24000,
                                        "text": sentence,
                                        "turn_id": tts_turn_id,
                                    })
                                else:
                                    # ffmpeg decode failed; fall back to native playback
                                    # but stop browser PCM first to prevent overlap.
                                    self._emit("jarvis_stop_pcm", {})
                                    is_wav = audio_bytes.startswith(b"RIFF")
                                    suffix = ".wav" if is_wav else ".mp3"
                                    tts_state["emitted"] = True
                                    dur = max(2.0, len(audio_bytes) / 32000.0)
                                    self._tts_playback_until = max(self._tts_playback_until, time.time() + dur + 0.5)
                                    self._play_audio_natively(audio_bytes, suffix=suffix, wait=True)
                except Exception as e:
                    print(f"[desktop] TTS synthesis pipeline error: {e}")
                finally:
                    tts_text_queue.task_done()

        def tts_playback_worker():
            while True:
                item = tts_audio_queue.get()
                if item is None:
                    tts_audio_queue.task_done()
                    break
                try:
                    turn = item.get("turn_id")
                    with self._tts_turn_lock:
                        if self._tts_turn_id != turn:
                            continue
                    tts_state["emitted"] = True
                    # Emit to UI immediately
                    self._emit("jarvis_audio_chunk", {
                        "audio": item["audio_b64"],
                        "text": item["sentence"],
                        "turn_id": turn,
                        "native_played": True,
                    })
                    # Play natively on Linux speakers and wait for sentence to finish.
                    # Subsequent sentence is already pre-synthesized and starts within <50ms!
                    self._play_audio_natively(item["audio_bytes"], suffix=item["suffix"], wait=True)
                except Exception as e:
                    print(f"[desktop] TTS playback pipeline error: {e}")
                finally:
                    tts_audio_queue.task_done()

        synth_thread = None
        playback_thread = None
        if self._voice:
            synth_thread = threading.Thread(target=tts_synthesis_worker, daemon=True)
            playback_thread = threading.Thread(target=tts_playback_worker, daemon=True)
            synth_thread.start()
            playback_thread.start()

        def queue_spoken_text(text: str):
            """Queue complete natural phrases, never individual LLM tokens."""
            nonlocal sent_count
            if not text:
                return
            clean = re.sub(r'^(?:ASSISTANT|AI)\s*:\s*', '', text, flags=re.IGNORECASE).strip()
            # Strip roleplay stage directions and action asterisks (*grins*, *cracks knuckles*, etc.)
            clean = re.sub(r'\*[^*]+\*', '', clean)
            clean = re.sub(r'\([^)]*(?:chuckle|grin|laugh|smirk|sigh|snicker|wink|shrug|cough|cracks|leans|snort)[^)]*\)', '', clean, flags=re.IGNORECASE)
            clean = re.sub(r'\s+([.,!?;:])', r'\1', clean)
            clean = re.sub(r'[ \t]+', ' ', clean).strip()
            if not clean:
                return
            # Keep normal sentences intact. For unusually long sentences, split only
            # at natural punctuation so Fish never receives an awkward fragment.
            parts = re.split(r'(?<=[,;:])\s+(?=[A-Z0-9])', clean) if len(clean) > 240 else [clean]
            for part in parts:
                part = part.strip()
                if len(part) > 3:
                    sent_count += 1
                    tts_text_queue.put(part)

        def emit_clean_chunk(tok: str):
            nonlocal sentence_buffer, sent_count
            self._emit("jarvis_stream_chunk", {"chunk": tok})
            if self._voice:
                sentence_buffer += tok
                # Fast sentence boundary: trigger TTS with natural cadence.
                # 1. Full sentence end boundary with acronym and decimal protection:
                sentence_regex = r'(?:(?<!\b[A-Z])(?<!\d)[.!?]+(?=\s+[A-Z0-9"\'\u201c\u2018]|\n|$|\s*$))'
                m = re.search(sentence_regex, sentence_buffer)
                if m and m.end() >= 12:
                    sentence = sentence_buffer[:m.end()].strip()
                    sentence_buffer = sentence_buffer[m.end():].lstrip()
                    if len(sentence) > 3:
                        clean_sentence = re.sub(r'(\w+)_(\w+)', r'\1 \2', sentence).replace('_', ' ')
                        queue_spoken_text(clean_sentence)
                    return

                # 2. Natural Clause Boundary:
                # Require at least 38 characters before splitting at a clause break (comma, colon, semicolon, dash).
                # Introductory phrases ("Hey, Sir,") stay together with the sentence without awkward network pauses.
                if len(sentence_buffer) >= 38:
                    clause_m = re.search(r'([,;:\u2014\-]+)\s', sentence_buffer)
                    if clause_m and clause_m.end() >= 35:
                        sentence = sentence_buffer[:clause_m.end()].strip()
                        sentence_buffer = sentence_buffer[clause_m.end():].lstrip()
                        if len(sentence) > 3:
                            clean_sentence = re.sub(r'(\w+)_(\w+)', r'\1 \2', sentence).replace('_', ' ')
                            queue_spoken_text(clean_sentence)
                        return

                # 3. Word boundary safety flush for long unpunctuated output (110+ chars)
                if len(sentence_buffer) > 110:
                    last_space = sentence_buffer.rfind(' ', 0, len(sentence_buffer) - 1)
                    if last_space > 40:
                        sentence = sentence_buffer[:last_space].strip()
                        sentence_buffer = sentence_buffer[last_space:].lstrip()
                        if len(sentence) > 3:
                            clean_sentence = re.sub(r'(\w+)_(\w+)', r'\1 \2', sentence).replace('_', ' ')
                            queue_spoken_text(clean_sentence)

        prefix_filter = _StreamingPrefixFilter(emit_clean_chunk)

        def on_token(chunk: str):
            prefix_filter.feed(chunk)

        try:
            result = self._voice.chat(question, self._target, on_token=on_token)
        except Exception as e:
            print(f"[desktop] Voice chat execution error: {e}")
            result = {
                "text": f"Sorry, partner. Hit a slight snag processing that: {str(e)}",
                "error": True,
                "mode": "advisor"
            }

        prefix_filter.flush()

        if result.get("rate_limited"):
            self._emit("rate_limited", {})

        # Flush any remaining sentence buffer
        if sentence_buffer.strip() and self._voice:
            frag = sentence_buffer.strip()
            frag = re.sub(r'^(?:ASSISTANT|AI)\s*:\s*', '', frag, flags=re.IGNORECASE).strip()
            if len(frag) > 2:
                queue_spoken_text(frag)

        # System skill / non-streamed response TTS & text streaming fallback: if no streaming chunks were generated,
        # simulate word-by-word text streaming on screen AND enqueue clean spoken sentences for local voice engine!
        if sent_count == 0 and result.get("text"):
            raw_text = result["text"]
            raw_text = re.sub(r'^(?:ASSISTANT|AI)\s*:\s*', '', raw_text, flags=re.IGNORECASE).strip()
            # 1. Simulate streaming text on screen word-by-word
            words = raw_text.split(' ')
            for i, w in enumerate(words):
                token = w + (" " if i < len(words) - 1 else "")
                self._emit("jarvis_stream_chunk", {"chunk": token})
                time.sleep(0.003)  # 3ms ultra-fast word typing effect

            # 2. Extract clean printable sentences for speech synthesis
            if self._voice:
                clean_lines = []
                for line in raw_text.splitlines():
                    line_s = line.strip()
                    if not line_s:
                        continue
                    line_s = re.sub(r'^(?:\d+\.|\bullet|[\*\-\+])\s*', '', line_s).strip()
                    if line_s:
                        clean_lines.append(line_s)
                
                full_clean = ". ".join(clean_lines)
                sentences = re.split(r'(?<=[.!?])\s+', full_clean)
                for s in sentences:
                    s_clean = s.strip()
                    if len(s_clean) > 3:
                        tts_text_queue.put(s_clean)

        if synth_thread:
            # Let the pipelined workers drain the queue cleanly in the background.
            tts_text_queue.put(None)
            # Only trigger browser synthetic fallback if Fish Audio is completely unconfigured
            fish_configured = bool(self._voice and getattr(self._voice, "fish_audio_available", False))
            if not fish_configured:
                synth_thread.join(timeout=2.0)
                if not tts_state["emitted"] and result.get("text"):
                    fallback_text = self._voice._sanitize_text_for_speech(result.get("text", "")) if self._voice else result.get("text", "")
                    self._emit("jarvis_tts_browser_fallback", {"text": fallback_text, "turn_id": tts_turn_id})

        # Post-hoc grounding check audit on full assembled LLM response text
        if self._target and result.get("text"):
            try:
                from narrative.grounding_check import verify_grounding
                _, warnings = verify_grounding(result["text"], self._target)
                if warnings:
                    print(f"[desktop] Grounding audit warning for active case {self._target.primary}: {warnings}")
                    self._emit("jarvis_grounding_warning", {"warnings": warnings, "target": self._target.primary})
            except Exception as e:
                print(f"[desktop] Post-hoc grounding check audit notice: {e}")

        self._emit("jarvis_answer", {
            "text": result.get("text", "Done."),
            "audio": None,
            "rate_limited": result.get("rate_limited", False),
            "mode": result.get("mode", "advisor"),
            "error": result.get("error", False),
            "open_dialog": result.get("open_dialog", False),
            "show_panel": result.get("show_panel", False),
            "search_query": result.get("search_query", "")
        })
        if result.get("open_dialog"):
            self._emit("open_investigate_dialog", {})
        if result.get("show_panel"):
            # Search tasks have a dedicated floating TaskSurface. Never open the
            # legacy fixed action panel for the same operation.
            is_search_result = bool(result.get("search_query")) or bool(
                result.get("panel_payload", {}).get("mode") == "SEARCH"
                if isinstance(result.get("panel_payload"), dict) else False
            )
            if not is_search_result:
                self._emit("open_jarvis_panel", {
                    "query": result.get("search_query", ""),
                    "text": result["text"],
                    "structured_payload": result.get("panel_payload", {})
                })
        if "Generating HTML investigation report" in result.get("text", ""):
            self.export_report()
        self._wake_window_expires = time.time() + 15.0

    def export_report(self) -> str:
        """Export current investigation target findings to a standalone HTML report."""
        if not self._target:
            msg = "No active investigation case loaded to export."
            self._emit("jarvis_answer", {"text": msg, "mode": "advisor"})
            return msg
        try:
            from exporters.html_report import generate
            report_path = generate(self._target)
            msg = f"HTML investigation report generated successfully at: {report_path}"
            print(f"[desktop] {msg}")
            self._emit("jarvis_answer", {"text": f"Report exported for {self._target.primary}. Saved to: {report_path}", "mode": "investigation"})
            return str(report_path)
        except Exception as e:
            err_msg = f"Failed to export report: {e}"
            print(f"[desktop] {err_msg}")
            self._emit("jarvis_answer", {"text": err_msg, "mode": "advisor"})
            return err_msg

    def pick_image(self):
        """Open native file dialog to select an image for analysis."""
        if not self._window:
            return
        try:
            file_types = ('Image Files (*.png;*.jpg;*.jpeg;*.gif;*.webp)', 'All files (*.*)')
            dialog_type = getattr(webview, 'OPEN_DIALOG', 10) if webview else 10
            result = self._window.create_file_dialog(dialog_type, allow_multiple=False, file_types=file_types)
            if result and len(result) > 0:
                src_path = Path(result[0])
                if src_path.exists():
                    attachments_dir = ROOT.parent / "cases" / "attachments"
                    attachments_dir.mkdir(parents=True, exist_ok=True)
                    dest_path = attachments_dir / src_path.name
                    import shutil
                    shutil.copy(src_path, dest_path)
                    rel_path = f"cases/attachments/{src_path.name}"
                    self._emit("image_selected", {"path": rel_path, "filename": src_path.name})
        except Exception as e:
            print(f"[desktop] pick_image error: {e}")

    def save_dropped_image(self, data_url: str, filename: str):
        """Save base64 data URL image dropped or picked via web file input."""
        try:
            import base64
            if "," in data_url:
                data_url = data_url.split(",", 1)[1]
            raw_bytes = base64.b64decode(data_url)
            attachments_dir = ROOT.parent / "cases" / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            dest_path = attachments_dir / filename
            dest_path.write_bytes(raw_bytes)
            rel_path = f"cases/attachments/{filename}"
            self._emit("image_selected", {"path": rel_path, "filename": filename})
        except Exception as e:
            print(f"[desktop] save_dropped_image error: {e}")

    def submit_image(self, image_path: str, prompt: str):
        """Analyze an attached image with prompt via JarvisVoice multimodal AI."""
        print(f"\n[desktop] Submitting image prompt: {prompt} (image: {image_path})")
        threading.Thread(target=self._run_submit_image, args=(image_path, prompt), daemon=True).start()

    def _run_submit_image(self, image_path: str, prompt: str):
        self._emit("jarvis_stream_start", {})
        def on_token(chunk: str):
            self._emit("jarvis_stream_chunk", {"chunk": chunk})

        full_prompt = prompt if prompt else "Analyze this image in detail and tell me what you observe from an OSINT investigator perspective."
        result = self._voice.chat(full_prompt, self._target, on_token=on_token, image_path=image_path)
        if result.get("rate_limited"):
            self._emit("rate_limited", {})

        self._emit("jarvis_answer", {
            "text": result["text"],
            "audio": None,
            "rate_limited": result.get("rate_limited", False),
            "mode": result.get("mode", "advisor"),
            "error": result.get("error", False)
        })

    async def _on_status(self, msg: str):
        self._emit("scan_status", {"message": msg})

    async def _on_find(self, entity, target):
        self._target = target
        self._last_entity = entity  # Track for false-positive command
        
        is_verified = entity.metadata.get("verified")
        conf = entity.confidence
        
        # Real-time UI status emission (No per-finding SLM calls — single closing monologue runs at end)
        url_val = (
            entity.metadata.get("url", "")
            or entity.metadata.get("profile", "")
            or entity.metadata.get("source_url", "")
            or entity.metadata.get("link", "")
        )

        self._emit("entity_found", {
            "type": entity.entity_type,
            "value": entity.value,
            "platform": entity.platform or "",
            "confidence": conf,
            "url": url_val,
            "verified": is_verified,
            "quote": None,
            "should_narrate": False,
            "screenshot_path": entity.metadata.get("screenshot_path"),
            "avatar_path": entity.metadata.get("avatar_path"),
            "metadata": entity.metadata,
        })

    async def _on_done(self, target, aborted=False):
        self._target = target
        error = False
        if aborted:
            text = "Investigation aborted. You pulled me away. But I remember what we found so far."
            used_gemini = False
        else:
            self._emit("jarvis_stream_start", {})
            def on_token(chunk: str):
                self._emit("jarvis_stream_chunk", {"chunk": chunk})

            result = self._voice.closing_monologue(target, on_token=on_token)
            text = result["text"]
            used_gemini = result.get("used_gemini", False)
            error = result.get("error", False)
            if result.get("rate_limited"):
                self._emit("rate_limited", {})

        self._memory.add("jarvis", text)
        self._emit("investigation_done", {
            "target": target.to_dict(),
            "monologue": text,
            "audio": None,
            "used_gemini": used_gemini,
            "error": error
        })

    def transcribe_audio(self, audio_b64: str) -> dict:
        """
        Receives base64 audio payload from frontend, converts to WAV via ffmpeg,
        and transcribes text using speech_recognition.
        """
        import base64
        import tempfile
        import subprocess
        import os
        try:
            import speech_recognition as sr
        except ImportError:
            return {"success": False, "error": "speech_recognition package not installed"}

        if not audio_b64:
            return {"success": False, "error": "Empty audio payload"}

        in_path = None
        out_path = None
        try:
            if "," in audio_b64:
                audio_b64 = audio_b64.split(",", 1)[1]

            raw_bytes = base64.b64decode(audio_b64)
            
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp_in:
                tmp_in.write(raw_bytes)
                in_path = tmp_in.name

            out_path = in_path + ".wav"

            cmd = ["ffmpeg", "-y", "-i", in_path, "-ac", "1", "-ar", "16000", out_path]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            target_file = out_path if (os.path.exists(out_path) and os.path.getsize(out_path) > 0) else in_path

            recognizer = sr.Recognizer()
            with sr.AudioFile(target_file) as source:
                audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data)

            return {"success": True, "text": text}
        except sr.UnknownValueError:
            return {"success": False, "error": "Speech was unintelligible"}
        except sr.RequestError as e:
            return {"success": False, "error": f"Speech API error: {e}"}
        except Exception as e:
            print(f"[desktop] Audio transcription error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            for p in (in_path, out_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    @staticmethod
    def normalize_wav_audio(wav_path: str, target_peak: int = 24000) -> bool:
        """Normalize 16-bit PCM WAV to target peak volume for crystal-clear STT."""
        try:
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 44:
                return False
            with wave.open(wav_path, "rb") as wf:
                params = wf.getparams()
                raw = wf.readframes(wf.getnframes())
            if not raw:
                return False
            count = len(raw) // 2
            shorts = struct.unpack(f"<{count}h", raw)
            max_val = max(abs(s) for s in shorts) if shorts else 0
            if max_val < 650:
                # Disregard background silence / ambient room hum (do not amplify pure noise)
                return False
            gain = min(25.0, float(target_peak) / float(max_val))
            if gain > 1.05:
                boosted = [int(max(-32768, min(32767, s * gain))) for s in shorts]
                boosted_raw = struct.pack(f"<{count}h", *boosted)
                with wave.open(wav_path, "wb") as wf:
                    wf.setparams(params)
                    wf.writeframes(boosted_raw)
                print(f"[audio] Normalized audio: gain {gain:.1f}x applied (peak: {max_val} -> {target_peak})")
            return True
        except Exception as e:
            print(f"[audio] Normalization notice: {e}")
            return False

    def shutdown(self):
        """Cleanly stop background mic, wake engine, and voice listener threads."""
        print("[desktop] Performing clean shutdown of all background services...")
        self._bg_voice_active = False
        if hasattr(self, '_wake_engine') and self._wake_engine:
            try:
                self._wake_engine.stop()
            except Exception:
                pass
        if hasattr(self, '_mic_proc') and self._mic_proc:
            try:
                self._mic_proc.terminate()
                self._mic_proc.kill()
            except Exception:
                pass

    def start_native_mic(self):
        """Start native Linux microphone recording via verified ffmpeg/arecord/rec background process."""
        if hasattr(self, '_mic_proc') and self._mic_proc and self._mic_proc.poll() is None:
            return {"success": True, "recording": True}

        wav_path = "/tmp/jarvis_mic_rec.wav"
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass

        candidates = [
            ["arecord", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1", wav_path],
            ["arecord", "-D", "plughw:1,0", "-f", "S16_LE", "-r", "16000", "-c", "1", wav_path],
            ["ffmpeg", "-y", "-f", "alsa", "-i", "default", "-ar", "16000", "-ac", "1", wav_path],
            ["ffmpeg", "-y", "-f", "pulse", "-i", "default", "-ar", "16000", "-ac", "1", wav_path],
            ["rec", "-r", "16000", "-c", "1", wav_path],
        ]

        last_err = ""
        for cmd in candidates:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                time.sleep(0.15)
                if proc.poll() is None:
                    self._mic_proc = proc
                    self._mic_start_time = time.time()
                    print(f"[desktop] Native mic recording active via {cmd[0]}...")
                    return {"success": True, "recording": True}
                else:
                    _, err_bytes = proc.communicate()
                    last_err = err_bytes.decode('utf-8', errors='ignore')
                    print(f"[desktop] Mic cmd {cmd[0]} exited prematurely: {last_err[:150]}")
            except Exception as e:
                last_err = str(e)
                print(f"[desktop] Failed launching mic candidate {cmd[0]}: {e}")

        return {"success": False, "error": f"Failed starting microphone recording: {last_err[:150]}"}

    def stop_native_mic(self):
        """Stop native Linux microphone recording and transcribe using Google Speech Recognition."""
        if not hasattr(self, '_mic_proc') or not self._mic_proc:
            return {"success": False, "error": "No microphone recording in progress"}

        # Ensure minimum 1.2s audio capture to prevent 0-byte recording on fast clicks
        if hasattr(self, '_mic_start_time'):
            elapsed = time.time() - self._mic_start_time
            if elapsed < 1.2:
                time.sleep(1.2 - elapsed)

        try:
            self._mic_proc.terminate()
            try:
                self._mic_proc.wait(timeout=1.5)
            except Exception:
                self._mic_proc.kill()
        except Exception as e:
            print(f"[desktop] Terminate mic process error: {e}")
        finally:
            self._mic_proc = None

        wav_path = "/tmp/jarvis_mic_rec.wav"
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            return {"success": False, "error": "No audio captured from microphone"}

        # Boost quiet microphone input before transcribing
        self.normalize_wav_audio(wav_path)

        try:
            recognizer = sr.Recognizer()
            recognizer.operation_timeout = 15.0
            text = ""
            start_stt = time.time()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                try:
                    text = recognizer.recognize_google(audio_data, language="en-IN").strip()
                except sr.UnknownValueError:
                    try:
                        text = recognizer.recognize_google(audio_data, language="en-US").strip()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[desktop] Push-to-talk STT notice: {e}")

            if text:
                print(f"[desktop] Push-to-talk transcribed in {time.time()-start_stt:.2f}s: '{text}'")
                return {"success": True, "text": text}
            else:
                print("[desktop] Push-to-talk: Speech was unintelligible or low volume.")
                return {"success": False, "error": "Speech was unintelligible"}
        except sr.RequestError as e:
            print(f"[desktop] Speech API network error: {e}")
            return {"success": False, "error": f"Speech API error: {e}"}
        except Exception as e:
            print(f"[desktop] Native mic transcribe error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

    def play_native_audio(self, audio_b64: str, sample_rate: int = 24000):
        """Fallback native speaker playback if browser Web Audio is suspended."""
        try:
            raw_pcm = base64.b64decode(audio_b64)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp_wav = tf.name
            with wave.open(tmp_wav, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate or 24000)
                wf.writeframes(raw_pcm)

            cmd = None
            if shutil.which("aplay"):
                cmd = ["aplay", "-q", tmp_wav]
            elif shutil.which("ffplay"):
                cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_wav]
            elif shutil.which("mpg123"):
                cmd = ["mpg123", "-q", tmp_wav]

            if cmd:
                subprocess.Popen(cmd)
                approx_dur = max(1.0, len(raw_pcm) / (sample_rate * 2))
                self._tts_playback_until = time.time() + approx_dur
        except Exception as e:
            print(f"[desktop] Native audio playback fallback notice: {e}")

    def start_background_voice_listener(self):
        """Start a background daemon thread that continuously listens for speech."""
        if hasattr(self, '_bg_voice_thread') and self._bg_voice_thread and self._bg_voice_thread.is_alive():
            return {"success": True, "status": "running"}

        self._bg_voice_active = True
        self._bg_voice_thread = threading.Thread(target=self._bg_voice_loop, daemon=True)
        self._bg_voice_thread.start()
        print("[desktop] Automatic background voice listener activated...")
        return {"success": True, "status": "started"}

    def _bg_voice_loop(self):
        """Continuous voice capture fed by the unified microphone pipeline.
        Picks up speech, runs adaptive VAD, and transcribes voice commands."""
        if not sr:
            print("[desktop] Voice listener disabled: speech_recognition package not installed.")
            return

        print("[desktop] Background voice listener activated via unified microphone pipeline.")

        ambient_energy = 150.0
        is_speaking = False
        speech_start_count = 0
        silence_chunks = 0
        pcm_buffer = []
        pre_roll = []

        # PyAudio chunk is 1280 samples (2560 bytes) = 80ms @ 16kHz
        cfg_pipeline = getattr(self, '_cfg', {}).get("audio_pipeline", {}) if hasattr(self, '_cfg') else {}
        hangover_ms = cfg_pipeline.get("speech_hangover_ms", 800)
        silence_hangover_chunks = max(4, hangover_ms // 80)     # ~800ms trailing pause for prompt response
        max_turn_chunks = 125            # 10s safety ceiling
        pre_roll_limit = 10              # 800ms preserves the first syllable

        try:
            while getattr(self, '_bg_voice_active', False):
                # Never record J.A.R.V.I.S.'s own TTS as a new command.
                if time.time() < getattr(self, '_tts_playback_until', 0.0):
                    pcm_buffer.clear(); pre_roll.clear()
                    is_speaking = False; silence_chunks = 0; speech_start_count = 0
                    time.sleep(0.05)
                    continue

                raw_chunk = None
                try:
                    raw_chunk = self._shared_audio_queue.get(timeout=0.1)
                except queue.Empty:
                    pass

                # If shared audio queue hasn't started yet, sleep briefly
                if not raw_chunk:
                    time.sleep(0.02)
                    continue

                count = len(raw_chunk) // 2
                if count == 0:
                    continue
                shorts = struct.unpack(f"<{count}h", raw_chunk)
                energy = (sum(x * x for x in shorts) / count) ** 0.5 if shorts else 0.0

                pre_roll.append(raw_chunk)
                if len(pre_roll) > pre_roll_limit:
                    pre_roll.pop(0)

                if not is_speaking:
                    ambient_energy = 0.985 * ambient_energy + 0.015 * energy

                # Voice activity threshold: reject ambient room noise (~200-280) and require genuine vocal acoustic energy (600+)
                threshold = max(380.0, ambient_energy * 1.6 + 60.0)
                speech = energy >= threshold

                if speech:
                    speech_start_count += 1
                    silence_chunks = 0
                    if not is_speaking and speech_start_count >= 2:
                        is_speaking = True
                        pcm_buffer = list(pre_roll)
                        print(
                            f"[voice listener] Voice detected (Energy: {energy:.1f} > "
                            f"Threshold: {threshold:.1f}) -> LISTENING"
                        )
                        self._emit("jarvis_speech_started", {})
                    if is_speaking:
                        pcm_buffer.append(raw_chunk)
                else:
                    speech_start_count = 0
                    if is_speaking:
                        pcm_buffer.append(raw_chunk)
                        silence_chunks += 1
                        if silence_chunks >= silence_hangover_chunks:
                            is_speaking = False
                            captured_pcm = b"".join(pcm_buffer)
                            pcm_buffer = []
                            silence_chunks = 0
                            duration_sec = len(captured_pcm) / 32000.0
                            print(
                                f"[voice listener] Speech completed. "
                                f"Captured {duration_sec:.1f}s of audio. Transcribing immediately..."
                            )
                            if len(captured_pcm) >= 12000:
                                self._emit("jarvis_transcribing", {"duration": duration_sec})
                                threading.Thread(
                                    target=self._process_captured_speech,
                                    args=(captured_pcm,), daemon=True
                                ).start()
                            else:
                                self._emit("jarvis_speech_ended", {})

                if is_speaking and len(pcm_buffer) >= max_turn_chunks:
                    print("[voice listener] Speech reached safety ceiling; transcribing current turn.")
                    is_speaking = False
                    captured_pcm = b"".join(pcm_buffer)
                    pcm_buffer = []
                    silence_chunks = 0
                    speech_start_count = 0
                    if captured_pcm:
                        self._emit("jarvis_transcribing", {"duration": len(captured_pcm) / 32000.0})
                        threading.Thread(
                            target=self._process_captured_speech,
                            args=(captured_pcm,), daemon=True
                        ).start()

        except Exception as e:
            import traceback
            print(f"[desktop] Background voice loop error: {e}")
            traceback.print_exc()
        finally:
            if proc:
                try:
                    proc.terminate(); proc.wait(timeout=1.0)
                except Exception:
                    try: proc.kill()
                    except Exception: pass

    def _process_captured_speech(self, pcm_bytes: bytes):
        """Transcribe captured speech and trigger HUD / JarvisVoice response."""
        # 1. Ignore audio captured while TTS was playing back
        if time.time() < getattr(self, '_tts_playback_until', 0.0):
            print("[voice listener] Captured audio ignored: TTS audio was active during recording.")
            self._emit("jarvis_speech_ended", {})
            return

        wav_path = f"/tmp/jarvis_speech_{int(time.time()*1000)}.wav"
        try:
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm_bytes)

            # Boost quiet microphone input before transcribing
            self.normalize_wav_audio(wav_path)

            recognizer = sr.Recognizer()
            recognizer.operation_timeout = 15.0
            text = ""
            start_stt = time.time()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                try:
                    text = recognizer.recognize_google(audio_data, language="en-IN").strip()
                except sr.UnknownValueError:
                    try:
                        text = recognizer.recognize_google(audio_data, language="en-US").strip()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[voice listener] STT notice: {e}")

            if text:
                print(f"[voice listener] Recognized text in {time.time()-start_stt:.2f}s: '{text}'")

                # 2. Filter out self-echo (mic picking up J.A.R.V.I.S.'s own voice)
                rec_clean = re.sub(r'[^\w\s]', '', text.lower()).strip()
                is_self_echo = False
                for past_resp in getattr(self, '_recent_agent_responses', []):
                    past_clean = re.sub(r'[^\w\s]', '', past_resp.lower()).strip()
                    if not past_clean or not rec_clean:
                        continue
                    if rec_clean in past_clean or past_clean in rec_clean:
                        is_self_echo = True
                        break
                    rec_words = set(rec_clean.split())
                    past_words = set(past_clean.split())
                    if rec_words and past_words:
                        overlap = len(rec_words & past_words) / len(rec_words)
                        if overlap > 0.6 and len(rec_words) >= 3:
                            is_self_echo = True
                            break

                if is_self_echo:
                    print(f"[voice listener] Self-echo suppressed (recognized text matches J.A.R.V.I.S. response): '{text}'")
                    self._emit("jarvis_speech_ended", {})
                    return

                # Check for explicit Stop commands first
                stop_pattern = r'\b(?:stop|shut\s*up|be\s*quiet|quiet|hush|silence|cancel)\b'
                if re.search(stop_pattern, text, re.IGNORECASE):
                    print(f"[voice listener] Stop command detected: '{text}'")
                    self._emit("jarvis_stop_command", {"text": text})
                    self._wake_window_expires = 0.0
                    return

                # Wake word patterns: J.A.R.V.I.S. + J.A.R.V.I.S. + natural addressing
                # Explicit/natural assistant addressing. These are intentionally
                # PREFIX-only so ordinary speech such as "my buddy called me"
                # cannot wake the assistant.
                # Wake word patterns: J.A.R.V.I.S.
                jarvis_pattern = r'^(?:(?:hey|hi|hai|yo|yoo|hello|ok|okay)\\s+)?(?:jarvis|jarv|javis)\\b\\s*,?\\s*'
                jarvis_anywhere = r'\\b(?:jarvis|jarv)\\b'
                jarvis_match = re.search(jarvis_pattern, text, re.IGNORECASE)
                anywhere_match = re.search(jarvis_anywhere, text, re.IGNORECASE) 
                now = time.time()

                # Direct identity / interaction questions bypass wake word check
                implicit_match = re.search(r'\\b(?:who\\s+are\\s+you|who\\s+are\\s+u|who\\s+u\\s+are|what\\s+can\\s+you\\s+do|who\\s+the\\s+fuck\\s+are\\s+you)\\b', text, re.IGNORECASE)

                if jarvis_match:
                    active_match = jarvis_match
                    address_name = "Hey JARVIS"
                    clean = text[active_match.end():].strip()
                    print(f"[voice listener] {address_name} match! Raw: '{text}', Clean command: '{clean}'")
                    self._emit("jarvis_wake_word_detected", {"raw": text, "clean": clean if clean else text})
                    if clean:
                        print(f"[voice listener] Sending voice command to assistant: '{clean}'")
                        self._emit("jarvis_voice_detected", {"text": clean, "raw": text})
                        self._wake_window_expires = now + 20.0
                    else:
                        print(f"[voice listener] Address only spoken ('{text}'). Opening 20s conversation window...")
                        self._wake_window_expires = now + 20.0
                elif anywhere_match:
                    print(f"[voice listener] Anywhere wake phrase match ('{text}')! Triggering assistant command...")
                    self._emit("jarvis_wake_word_detected", {"raw": text, "clean": text})
                    self._emit("jarvis_voice_detected", {"text": text, "raw": text})
                    self._wake_window_expires = now + 20.0
                elif implicit_match:
                    print(f"[voice listener] Direct query match ('{text}')! Triggering assistant command...")
                    self._emit("jarvis_wake_word_detected", {"raw": text, "clean": text})
                    self._emit("jarvis_voice_detected", {"text": text, "raw": text})
                    self._wake_window_expires = now + 20.0
                elif now < getattr(self, '_wake_window_expires', 0.0):
                    print(f"[voice listener] Active conversation window! Sending follow-up command to assistant: '{text}'")
                    self._emit("jarvis_voice_detected", {"text": text, "raw": text})
                    self._wake_window_expires = now + 20.0
                else:
                    print(f"[voice listener] No wake word detected in ambient audio: '{text}'")
                    self._emit("jarvis_speech_ended", {})
            else:
                print("[voice listener] Audio transcribed to empty text.")
                self._emit("jarvis_speech_ended", {})

        except sr.UnknownValueError:
            print("[voice listener] Audio was unintelligible.")
            self._emit("jarvis_speech_ended", {})
        except sr.RequestError as req_err:
            print(f"[voice listener] Google Speech Recognition API error: {req_err}")
            self._emit("jarvis_speech_ended", {})
        except Exception as e:
            import traceback
            print(f"[desktop] Processing error: {e}")
            traceback.print_exc()
            self._emit("jarvis_speech_ended", {})
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

    def _emit(self, event: str, data: dict):
        if self._window:
            try:
                def _json_default(obj):
                    if hasattr(obj, "to_dict"):
                        return obj.to_dict()
                    if hasattr(obj, "__dict__"):
                        return obj.__dict__
                    return str(obj)
                json_str = json.dumps(data, default=_json_default)
                js_code = f"(window.jarvis || window.joe) && (window.jarvis || window.joe).receive && (window.jarvis || window.joe).receive('{event}', {json_str})"
                self._window.evaluate_js(js_code)
            except Exception as e:
                print(f"[desktop] JS evaluate error for {event}: {e}")




class JarvisDesktop:
    def launch(self):
        import shutil

        # Copy icons and artwork assets to frontend execution directory
        src_icon = ROOT.parent / "assets" / "logo.png"
        if not src_icon.exists():
            src_icon = ROOT.parent / "assets" / "jarvis-icon.png"
        dst_icon = ROOT / "jarvis-icon.png"
        if src_icon.exists():
            shutil.copy(src_icon, dst_icon)
            shutil.copy(src_icon, ROOT / "jarvis-icon.png")

        src_geo = ROOT.parent / "assets" / "world_outline.jpg"
        dst_geo = ROOT / "world_outline.jpg"
        if src_geo.exists() and not dst_geo.exists():
            shutil.copy(src_geo, dst_geo)

        # Wire GTK desktop app window icon for Linux taskbar/dock/alt-tab
        if dst_icon.exists():
            try:
                import gi
                gi.require_version("Gtk", "3.0")
                from gi.repository import Gtk, GdkPixbuf
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(dst_icon))
                Gtk.Window.set_default_icon(pixbuf)
                print(f"[desktop] Bound GTK window & taskbar icon: {dst_icon}")
            except Exception as e:
                pass

        api = JarvisAPI()
        persona_name = str(api._cfg.get("persona", "jarvis")).strip().lower()
        win_title = "J.A.R.V.I.S. — Tactical Intelligence Console"
        window_kwargs = {
            "title": win_title,
            "url": str(HTML_PATH),
            "js_api": api,
            "width": 1200,
            "height": 780,
            "min_size": (900, 600),
            "background_color": "#030405",
        }

        if webview is None:
            raise ImportError("pywebview is required to run JarvisDesktop. Install pywebview or run with CLI mode.")

        # Native 3D Earth Globe is integrated directly into the spatial WebGL canvas;
        # Attach custom BottleServer to pywebview to reliably serve CCTV frames and ADS-B feeds (200 OK)
        server_cls = None
        try:
            import bottle
            try:
                from webview.http import BottleServer, is_app, is_local_url, _get_random_port, ThreadedAdapter
            except Exception:
                try:
                    from webview.http.bottle import BottleServer, is_app, is_local_url, _get_random_port, ThreadedAdapter
                except Exception:
                    BottleServer = None

            if BottleServer:
                class JarvisBottleServer(BottleServer):
                    @classmethod
                    def start_server(cls, urls, http_port, keyfile=None, certfile=None):
                        try:
                            import uuid, threading, os
                            from os.path import abspath
                            try:
                                from webview.http import is_app, is_local_url, _get_random_port, ThreadedAdapter
                            except Exception:
                                from webview.http.bottle import is_app, is_local_url, _get_random_port, ThreadedAdapter
                            from webview import _state

                            apps = [u for u in urls if is_app(u)]
                            server = cls()

                            if len(apps) > 0:
                                app = apps[0]
                                common_path = '.'
                            else:
                                local_urls = [u.split('#')[0] for u in urls if is_local_url(u)]
                                common_path = os.path.commonpath(local_urls) if len(local_urls) > 0 else None
                                if common_path is not None and not os.path.isdir(abspath(common_path)):
                                    common_path = os.path.dirname(common_path)
                                server.root_path = abspath(common_path) if common_path is not None else None
                                app = bottle.Bottle()

                                @app.post(f'/js_api/{server.uid}')
                                def js_api():
                                    bottle.response.headers['Access-Control-Allow-Origin'] = '*'
                                    bottle.response.headers['Access-Control-Allow-Methods'] = 'PUT, GET, POST, DELETE, OPTIONS'
                                    bottle.response.headers['Access-Control-Allow-Headers'] = 'Origin, Accept, Content-Type, X-Requested-With, X-CSRF-Token'
                                    body = json.loads(bottle.request.body.read().decode('utf-8'))
                                    if body['uid'] in server.js_callback:
                                        return json.dumps(server.js_callback[body['uid']](body))
                                    return ""

                                # Hook dynamic OSINT and CCTV endpoints BEFORE catch-all static route!
                                @app.route('/api/adsb/<feed>')
                                def _bottle_adsb(feed="mil"):
                                    bottle.response.content_type = 'application/json'
                                    return json.dumps(api.get_adsb_flights(feed))

                                @app.route('/api/cctv/frame/<camera_id>')
                                def _bottle_cctv(camera_id):
                                    bottle.response.content_type = 'image/jpeg'
                                    bottle.response.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                                    return api.get_cctv_frame(camera_id)

                                @app.route('/api/firms')
                                def _bottle_firms():
                                    bottle.response.content_type = 'application/json'
                                    return json.dumps(api.get_firms_hotspots())

                                @app.route('/')
                                @app.route('/<file:path>')
                                def asset(file):
                                    if not server.root_path:
                                        return ''
                                    bottle.response.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                                    bottle.response.set_header('Pragma', 'no-cache')
                                    bottle.response.set_header('Expires', 0)
                                    return bottle.static_file(file, root=server.root_path)

                            server.root_path = abspath(common_path) if common_path is not None else None
                            server.port = http_port or _get_random_port()
                            server.thread = threading.Thread(
                                target=lambda: bottle.run(
                                    app=app, server=ThreadedAdapter, port=server.port, quiet=not _state['debug']
                                ),
                                daemon=True,
                            )
                            server.thread.start()

                            server.running = True
                            server.address = f'http://127.0.0.1:{server.port}/'
                            cls.common_path = common_path
                            server.js_api_endpoint = f'{server.address}js_api/{server.uid}'

                            return server.address, common_path, server
                        except Exception as srv_err:
                            print(f"[desktop] JarvisBottleServer fallback notice: {srv_err}")
                            return super().start_server(urls, http_port, keyfile, certfile)

                server_cls = JarvisBottleServer
        except Exception as e:
            print(f"[desktop] Custom BottleServer setup notice: {e}")

        if server_cls:
            window_kwargs["server"] = server_cls

        window = webview.create_window(**window_kwargs)

        api.set_window(window)
        # Background voice listener will activate cleanly once the frontend signals pywebviewready

        # Patch pywebview PyQt6 permission policy enum bug (int vs QWebEnginePage.PermissionPolicy)
        try:
            import webview.platforms.qt as qt_mod
            from qtpy.QtWebEngineWidgets import QWebEnginePage
            policy_cls = getattr(QWebEnginePage, "PermissionPolicy", None)
            granted = getattr(policy_cls, "PermissionGrantedByUser", 1) if policy_cls else 1
            denied = getattr(policy_cls, "PermissionDeniedByUser", 2) if policy_cls else 2

            def _safe_onFeaturePermissionRequested(self, url, feature):
                feat_name = getattr(feature, "name", str(feature))
                if "Audio" in feat_name or "Video" in feat_name or "Geolocation" in feat_name:
                    self.setFeaturePermission(url, feature, granted)
                else:
                    self.setFeaturePermission(url, feature, denied)

            def _terminal_javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
                msg_level = getattr(QWebEnginePage, "JavaScriptConsoleMessageLevel", None)
                level_str = "LOG"
                color = "\033[36m"
                reset = "\033[0m"
                if msg_level:
                    if level == getattr(msg_level, "WarningMessageLevel", 1):
                        level_str = "WARN"
                        color = "\033[33m"
                    elif level == getattr(msg_level, "ErrorMessageLevel", 2):
                        level_str = "ERROR"
                        color = "\033[31;1m"
                    elif level == getattr(msg_level, "InfoMessageLevel", 0):
                        level_str = "INFO"
                        color = "\033[34m"

                src = os.path.basename(sourceID) if sourceID else "app.html"
                print(f"{color}[js:{level_str}]{reset} ({src}:{lineNumber}) {message}", flush=True)

            if hasattr(qt_mod, "BrowserView") and hasattr(qt_mod.BrowserView, "WebPage"):
                qt_mod.BrowserView.WebPage.onFeaturePermissionRequested = _safe_onFeaturePermissionRequested
                qt_mod.BrowserView.WebPage.javaScriptConsoleMessage = _terminal_javaScriptConsoleMessage
        except Exception as e:
            print(f"[desktop] Qt permission/console patch notice: {e}")

        # Debug mode for terminal alone: stream JS logs and errors to terminal without opening GUI DevTools window
        if hasattr(webview, "settings"):
            webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False

        def _on_closed():
            print("\n[desktop] Window closed by user. Performing clean shutdown of all threads...")
            try:
                api.shutdown()
            except Exception:
                pass
            os._exit(0)

        window.events.closed += _on_closed

        try:
            if server_cls:
                try:
                    webview.start(gui="qt", debug=True, server=server_cls)
                except TypeError:
                    webview.start(gui="qt", debug=True)
            else:
                webview.start(gui="qt", debug=True)
        finally:
            try:
                api.shutdown()
            except Exception:
                pass
            print("[desktop] J.A.R.V.I.S. process exited cleanly.")
            os._exit(0)




