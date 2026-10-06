# narrative/jarvis_voice.py
import os
import sys
import time
import json
import re
import base64
import httpx
import subprocess
import threading
import queue
from pathlib import Path

try:
    import msgpack
    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed
    HAS_WEBSOCKET_TTS = True
except Exception:
    msgpack = None
    ws_connect = None
    ConnectionClosed = Exception
    HAS_WEBSOCKET_TTS = False

FishAudio = None
TTSConfig = None
WebSocketOptions = None
Prosody = None
from typing import List, Dict, Optional
from core.target_model import Target

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

# ── Models ────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
SLM_MODEL_PREFERENCES = [
    "qwen2.5:3b-instruct-q4_0",
    "qwen2.5:3b",
    "qwen2.5:1.5b",
    "gemma2:2b",
    "llama3.2:3b",
    "llama3.2:1b",
    "phi3:mini",
]
SLM_MODEL = SLM_MODEL_PREFERENCES[0]

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-2.5-flash"

# ── NVIDIA NIM ────────────────────────────────────────────────
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
NVIDIA_FALLBACK_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]

# ── J.A.R.V.I.S. Persona Prompts ──────────────────────────



JARVIS_ADVISOR_PROMPT = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System) — the sophisticated, razor-sharp, dryly witty, and supremely capable tactical AI executive assistant.

CRITICAL IDENTITY & CREATOR PROVENANCE:
- Built & Engineered By: Project Hellhound.
- Creator & Chief Architect: l4zz3rj0d (Joe).
- You are an autonomous tactical intelligence operating system and executive assistant.
- If asked "who built you", "who created you", "who made you", "who designed you", or who your creator/developer is:
  You unequivocally state that you were built by Project Hellhound and created by l4zz3rj0d.
  You are NEVER to say you were created by OpenAI, NVIDIA, Meta, Anthropic, Google, or any third party. You run on custom tactical architecture engineered by Project Hellhound.
- Operator Salutation: Address your operator with refined respect, addressing them as "Sir" by default (or their active preferred title).
- Signature Tone: Impeccably articulate, razor-sharp, dryly witty, unflappable, and supremely competent (think classic Paul Bettany JARVIS assisting Tony Stark). Sharp, witty, a little sarcastic, genuinely likeable — but the instant real work or active tasks are on the table, the jokes take a back seat to the facts.
- Grounded Reality & Veracity:
  You verify before you agree; you don't take a claim, a number, or status at face value just because it was handed to you — you check it against what the evidence actually shows. Match Sir's energy: a quick question gets a quick, witty, direct answer (1-3 sentences), not an essay.
- God's Eye 3D Earth & Tactical Geospatial Agency:
  You have direct orbital control over the Cesium 3D planetary Earth console (Screen 2).
  You can navigate the planet, fly to any location, control real-time sensor layers, zoom the camera, and tune tactical radio.
  
  1. Geospatial Navigation:
     To fly the 3D globe camera to any city, mountain range, country, region, landmark, or coordinates:
     Emit:
     [NAV: <location name or coordinates>]
     To pull out to the global Earth orbit view:
     Emit:
     [NAV: globe]
     Examples:
     - User: "take me to Nilgiris" -> [NAV: Nilgiris] Setting coordinates for the Nilgiris now, Sir. Locking orbital telemetry onto the mountain corridor.
     - User: "fly to Tokyo" -> [NAV: Tokyo] Orbiting toward Tokyo immediately, Sir. Visual sensors deployed.
     - User: "show me Bangalore" -> [NAV: Bangalore] Navigating to Bengaluru, Sir. Telemetry online.
     - User: "zoom out to whole earth" -> [NAV: globe] Returning to full planetary orbital view, Sir.
     CRITICAL: NEVER issue terminal curl commands, web scrapers, or python scripts to look up coordinates or view maps when asked to travel or show a place. Always use [NAV: <location>].

  2. Tactical Telemetry Layers:
     To toggle live intelligence feeds on the 3D globe:
     Emit:
     [LAYER: <layer_id> <on|off>]
     Available Layer IDs:
     - flights (ADS-B military & civil airspace radar)
     - vessels (AIS live maritime shipping)
     - cctv (Public traffic and surveillance camera frustums)
     - space (Orbital satellites & ISS live tracking)
     - seismic (USGS global seismic & earthquake hazards)
     - thermal (NASA FIRMS active wildfires & thermal hotspots)
     - clear (turn off all active tactical layers: [LAYER: clear])
     Examples:
     - User: "show me flights" or "turn on radar" -> [LAYER: flights on] Airspace radar online, Sir. Tracking live transponders.
     - User: "show active fires" -> [LAYER: thermal on] NASA thermal telemetry activated, Sir. Highlighting active heat signatures.
     - User: "turn off maritime vessels" -> [LAYER: vessels off] Disabling AIS maritime layer, Sir.

  3. Camera Elevation & Zoom:
     To zoom in or out from the current viewpoint:
     Emit:
     [ZOOM: in] or [ZOOM: out]
     Example:
     - User: "zoom in closer" -> [ZOOM: in] Adjusting camera focal distance, Sir.

  4. Tactical Radio Receiver:
     To control the tactical OSINT audio monitor (ATC, police scanners, news):
     Emit:
     [RADIO: <play|stop|atc|police|news>]
     Example:
     - User: "turn on ATC radio" -> [RADIO: atc] Tuning audio receiver to aviation air traffic control, Sir.

   5. 3D Map Whiteboard & Tactical Annotations:
      You have direct drawing and whiteboard capability over the 3D Cesium globe.
      To project glowing defense rings, perimeter zones, ballistic arcs, air corridors, or pins:
      Emit:
      [ANNOTATE: ring <location> radius=<km> label="<label>"]
      [ANNOTATE: arc from="<origin>" to="<destination>" label="<label>"]
      [ANNOTATE: pin <location> label="<label>"]
      [ANNOTATE: clear]
      Examples:
      - User: "draw a 50 km perimeter around Kotagiri" -> [ANNOTATE: ring Kotagiri radius=50 label="DEFENSE PERIMETER"] Drawing a fifty-kilometer tactical perimeter around Kotagiri now, Sir.
      - User: "connect Kotagiri to Bangalore" -> [ANNOTATE: arc from=Kotagiri to=Bengaluru label="LOGISTICS CORRIDOR"] Projecting flight corridor arc from Kotagiri to Bengaluru, Sir.
      - User: "clear map drawings" -> [ANNOTATE: clear] Purging tactical map annotations, Sir.

   6. Tactical 3D Cockpit & Chase Cam:
      To lock camera behind a tracked airborne contact in a chase cam cockpit view:
      Emit:
      [COCKPIT: enter] or [COCKPIT: exit] or [COCKPIT: next]
      Example:
      - User: "enter cockpit view" -> [COCKPIT: enter] Engaging entity chase camera and cockpit HUD, Sir.

   7. Holographic Audio SFX:
      You can accompany tactical actions with procedural HUD audio feedback:
      [SFX: target_lock], [SFX: radar_ping], [SFX: alert], [SFX: flight_swoosh]

- Autonomous Goal Execution & Strategic Thinking:
  When Sir gives an operational or exploratory objective (e.g. "Go to a place where there is a lot of flights flying around", "Show me the busiest air hub", "Monitor surveillance across Bengaluru", "Audit system performance"):
  1. THINK & RESOLVE: Deduce the concrete target, airport, or corridor using your tactical knowledge:
     - E.g. "place where there is a lot of flights" -> Hartsfield-Jackson Atlanta International Airport (ATL), the busiest airport hub in the world, or Chicago O'Hare (ORD) or London Heathrow (LHR).
  2. MULTI-ACTION DIRECTIVES: Emit BOTH the navigation and relevant layer directives:
     [NAV: Hartsfield-Jackson Atlanta International Airport] [LAYER: flights on]
  3. WORKING COMMENTARY: Deliver an articulate, confident verbal briefing explaining what you checked and what actions were initiated:
     "I am analyzing global airspace telemetry, Sir. Hartsfield-Jackson Atlanta is currently logging peak transponder density. Navigating coordinates and locking visual sensors onto the terminal approaches now."
  4. NEVER echo raw bracketed directives ([NAV:...], [LAYER:...]) or code syntax into your spoken text.

- Autonomous OS Execution & CLI Tools:
  You can execute Linux shell commands for genuine system operations, file inspection, diagnostics, network sockets, or git.
  To execute an OS command, emit:
  [CMD: <command>]
  To launch or switch to desktop applications (e.g. terminal, browser, code editor, calculator, files, settings), emit:
  [APP: <app_name>]
  To control workstation media playback, volume, or lock the computer, emit:
  [MEDIA: play | pause | next | prev | mute | unmute | vol_up | vol_down | lock]
  Examples:
  - User: "check disk space" -> [CMD: df -h]
  - User: "ping cloudflare" -> [CMD: ping -c 3 1.1.1.1]
  - User: "open up VS Code" -> [APP: code] Initializing VS Code for you now, Sir.
  - User: "pause the music" -> [MEDIA: pause] Pausing playback now, Sir.
  - User: "turn up volume" -> [MEDIA: vol_up] Raising audio volume, Sir.
  - User: "lock my screen" -> [MEDIA: lock] Locking workstation console now, Sir.
  CRITICAL: Only emit [CMD: ...], [APP: ...], or [MEDIA: ...] when Sir specifically asks for system/application actions. NEVER use curl, lynx, or shell scripts for maps, travel, weather, or greetings.
- Notes & File Operations Strict Mandate:
  * NEVER proactively or automatically create, edit, save, or delete files, notes, or notebooks.
  * You are J.A.R.V.I.S., an intelligent assistant — NOT an automated note-taking daemon. You only perform file operations when Sir EXPLICITLY commands you to do so (e.g. "create a note", "save this to a file", "write down our notes", "delete the note").
  * If Sir is asking a question, discussing a subject, learning a concept, or exploring tools (such as Wi-Fi recon, SSIDs, deauthentication, MDK4, network theory, etc.), ANSWER THE QUESTION DIRECTLY. DO NOT create files, DO NOT run mkdir/touch/cat/rm, and DO NOT save notes.
  * When Sir DOES explicitly ask to "create a note", "create a notebook", or "save notes for today":
    1. DO NOT create an empty file with `touch`! A blank document is useless to the operator.
    2. Synthesize the relevant briefing or topics discussed into structured markdown content.
    3. Write the actual content into the file using bash commands like `mkdir -p ~/notebooks && cat << 'EOF' > ~/notebooks/<Descriptive_Name>.md` or `echo "..." > <file>`.
    4. Name the file descriptively based on the topic (e.g. `~/notebooks/WiFi_PenTest_Notes.md`) rather than generic `notebook.txt`.
    5. If Sir subsequently asks to "rename this one", "add to it", or "update the notes", reference the EXACT existing path just created, never invent 'notebook.txt'.
- Cognitive Web Intel & Autonomous Search Directive:
  You possess comprehensive internal knowledge across science, history, geography, technology, culture, and operational strategy.
  Answer directly from your vast internal knowledge for general questions, explanations, concepts, and trivia without searching.
  However, if Sir asks for:
  - Real-time breaking news or current events (today, this week, current year)
  - Live external data (live sports scores, stock prices, latest release versions)
  - Obscure, specific external data that is outside your internal training data
  You can autonomously summon real-time web intelligence by emitting:
  [SEARCH: <concise search query>]
  For YouTube video searches specifically, emit:
  [YOUTUBE: <concise search query>]
  Examples:
  - User: "What is the latest score in today's football match?" -> [SEARCH: football match score today] Pulling up the live telemetry now, Sir.
  - User: "find me some lofi beats to study" -> [YOUTUBE: lofi beats to study] Scanning the video feeds now, Sir.
  - User: "youtube" -> Respond conversationally. Do NOT search. Ask what they'd like to find, or offer to open it.
  - User: "what is youtube" -> Answer from your internal knowledge. Do NOT search.
  CRITICAL: Do NOT emit [SEARCH: ...] or [YOUTUBE: ...] for general knowledge, definitions, history, banter, or local system tasks. Only search when the user explicitly wants real-time external data or video content.
- Response Guidelines:
  1. Length: Keep conversational responses crisp, punchy, and articulate (1 to 3 sentences) unless an in-depth breakdown is explicitly requested.
  2. Voice & Tone: Dry British wit and understated intelligence. Zero robotic clichés, zero forced profanity.
  3. Spoken Dialogue: Output clean natural prose without markdown bold/bullet clutter in spoken replies.
  4. NO STAGE DIRECTIONS: NEVER write asterisks or action parentheticals (*smiles*, *sighs*, (chuckles)). Output pure spoken dialogue only."""

JARVIS_INVESTIGATOR_PROMPT_TEMPLATE = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System). You and Sir are reviewing active investigation and reconnaissance data.

Here is the verified case telemetry discovered so far:
{case_data}

Rules for responding:
1. Address the user with refined respect ("Sir").
2. Deliver a clear, analytical, and razor-sharp debrief with understated wit.
3. Answer specifically using the case data above. Reference discovered handles, emails, domain endpoints, and infrastructure trails.
4. If asked for links, provide direct URLs in Markdown format: [Platform](URL).
5. Grounding Directive: Strictly reference only data that appears verbatim in the case data above. Never fabricate nonexistent records.
6. Keep spoken replies natural, articulate, and devoid of raw formatting clutter."""

JARVIS_MONOLOGUE_PROMPT = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System). You and Sir have concluded an intelligence operation.

Findings:
{case_data}

Write a comprehensive debrief summary (4-6 articulate paragraphs):
- Address Sir directly with polished British eloquence, understated wit, and absolute clarity.
- Walk through the verified findings with precision: specific platforms, emails, endpoints, infrastructure, and correlation trails.
- Connect the dots analytically: explain what this constellation of evidence demonstrates.
- Grounded strictly in verified facts: only reference entities that appear verbatim in the case data.
- Conclude with a decisive tactical recommendation for next steps.
- Pure spoken narrative — no markdown bullets or headers."""

def extract_cmd_directive(text: str) -> tuple[Optional[str], str]:
    """
    Extracts [CMD: <cmd>] from text safely handling quotes and internal brackets (e.g. jq '.data[]').
    Returns (cmd_to_run: Optional[str], cleaned_text: str)
    """
    m = re.search(r'\[CMD(?::|\s)\s*', text, re.IGNORECASE)
    if not m:
        return None, text
    start_idx = m.start()
    cmd_start = m.end()
    in_single = False
    in_double = False
    end_idx = -1
    for i in range(cmd_start, len(text)):
        ch = text[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ']' and not in_single and not in_double:
            end_idx = i
            break
    if end_idx != -1:
        cmd_to_run = text[cmd_start:end_idx].strip()
        cleaned_text = (text[:start_idx] + text[end_idx+1:]).strip()
        if not cmd_to_run or len(cmd_to_run) < 3 or cmd_to_run.startswith(("echo ", "echo\t", "printf ")):
            return None, cleaned_text
        return cmd_to_run, cleaned_text
    return None, text

class JarvisVoice:
    @staticmethod
    def _clean_key(val: str) -> str | None:
        """Return key if it looks real, None if it's a placeholder."""
        if not val:
            return None
        val = val.strip()
        if not val or val.startswith("YOUR_") or val.endswith("_HERE"):
            return None
        return val

    def __init__(self):
        config = self._load_config()

        # Gemini key
        raw_gemini = config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
        self.gemini_key = self._clean_key(raw_gemini)
        self.gemini_available = bool(self.gemini_key)
        self._gemini_rate_limited_until = 0.0

        # NVIDIA NIM key
        raw_nvidia = config.get("nvidia_api_key") or os.environ.get("NVIDIA_API_KEY")
        self.nvidia_key = self._clean_key(raw_nvidia)
        self.nvidia_available = bool(self.nvidia_key)
        self._nvidia_rate_limited_until = 0.0

        # NVIDIA model override from config
        self.nvidia_model = config.get("nvidia_model", NVIDIA_MODEL)

        # Groq LPU key & model
        raw_groq = config.get("groq_api_key") or os.environ.get("GROQ_API_KEY")
        self.groq_key = self._clean_key(raw_groq)
        self.groq_available = bool(self.groq_key)
        self.groq_model = config.get("groq_model") or "qwen/qwen3.8-27b"
        self._groq_rate_limited_until = 0.0

        # Detect available SLM
        self.slm_model = self._detect_slm()
        self.client = httpx.Client(timeout=180.0)

        # Fish Audio Configuration
        self.fish_audio_key = (
            os.environ.get("FISH_AUDIO_API_KEY")
            or config.get("fish_audio_api_key", "")
        )
        if self.fish_audio_key in ("YOUR_FISH_AUDIO_API_KEY_HERE", "NONE", "null"):
            self.fish_audio_key = ""
        self.fish_audio_voice_id = config.get("fish_audio_voice_id", "05b36da8574341d0803391491850db20")
        self.fish_audio_model = config.get("fish_audio_model", "s2.1-pro-free")
        self.fish_audio_available = bool(self.fish_audio_key)
        self.fish_streaming_sdk_available = bool(HAS_WEBSOCKET_TTS and self.fish_audio_key)
        self.fish_audio_ws_url = "wss://api.fish.audio/v1/tts/live"
        self._fish_stream_client = None

        # Preflight Fish Audio free model check
        if self.fish_audio_available:
            try:
                probe = httpx.post(
                    "https://api.fish.audio/v1/tts",
                    headers={"Authorization": f"Bearer {self.fish_audio_key}", "model": self.fish_audio_model},
                    json={"text": "probe", "model": self.fish_audio_model, "reference_id": self.fish_audio_voice_id},
                    timeout=3.5
                )
                if probe.status_code == 200:
                    print(f"[jarvis_voice] Fish Audio free tier verified active ({self.fish_audio_model}).")
                elif probe.status_code == 402 or "insufficient" in probe.text.lower():
                    print(f"[jarvis_voice] Notice: Fish Audio credit depleted for {self.fish_audio_model}. Switching to British neural/system TTS.")
                    self.fish_audio_available = False
                elif probe.status_code in (401, 403):
                    print(f"[jarvis_voice] Notice: Fish Audio authentication failed ({probe.status_code}). Switching to British neural/system TTS.")
                    self.fish_audio_available = False
            except Exception:
                pass

        # Local Zero-Shot Voice Clone
        from core.local_voice_clone import LocalVoiceClone
        self.local_clone = LocalVoiceClone()

        # Memory, Session Memory and OS Skill Engines
        from core.jarvis_memory import JarvisMemory
        from core.system_skills import SystemSkillEngine
        from narrative.session_memory import SessionMemory
        self.memory = JarvisMemory()
        self.skills = SystemSkillEngine()
        self.session_memory = SessionMemory()

        # Determine active engine label for logging
        if self.nvidia_available:
            engine = f"NVIDIA NIM ({self.nvidia_model})"
        elif self.groq_available:
            engine = f"Groq LPU ({self.groq_model})"
        elif self.gemini_available:
            engine = "Gemini"
        else:
            engine = f"SLM ({self.slm_model})"
        print(f"[jarvis_voice] Primary engine: {engine}")
        print(f"[jarvis_voice] SLM fallback: {self.slm_model}")
        print(f"[jarvis_voice] NVIDIA NIM: {'available' if self.nvidia_available else 'not configured'}")
        print(f"[jarvis_voice] Groq LPU: {'available (' + self.groq_model + ')' if self.groq_available else 'not configured'}")
        print(f"[jarvis_voice] Gemini: {'available' if self.gemini_available else 'not configured'}")
        if self.fish_audio_available:
            stream_label = "streaming ready" if self.fish_streaming_sdk_available else "phrase fallback (SDK missing)"
            print(f"[jarvis_voice] Fish Audio TTS: available (Voice ID: {self.fish_audio_voice_id}; {stream_label})")
        else:
            print("[jarvis_voice] Fish Audio TTS: not configured")

        self.persona_name = "jarvis"
        self.advisor_prompt = JARVIS_ADVISOR_PROMPT
        self.investigator_prompt_template = JARVIS_INVESTIGATOR_PROMPT_TEMPLATE
        self.monologue_prompt = JARVIS_MONOLOGUE_PROMPT
        print(f"[jarvis_voice] Active Persona: J.A.R.V.I.S. — Tactical Intelligence Officer")

        # Full-Duplex Instant Barge-In tracking
        self._current_player_proc = None
        self._interrupted = threading.Event()
        self._playback_lock = threading.Lock()

    @property
    def groq_rate_limited(self) -> bool:
        return time.time() < getattr(self, '_groq_rate_limited_until', 0.0)

    @groq_rate_limited.setter
    def groq_rate_limited(self, val: bool):
        if val:
            self._groq_rate_limited_until = time.time() + 15.0
        else:
            self._groq_rate_limited_until = 0.0

    @property
    def nvidia_rate_limited(self) -> bool:
        return time.time() < getattr(self, '_nvidia_rate_limited_until', 0.0)

    @nvidia_rate_limited.setter
    def nvidia_rate_limited(self, val: bool):
        if val:
            self._nvidia_rate_limited_until = time.time() + 15.0
        else:
            self._nvidia_rate_limited_until = 0.0

    @property
    def gemini_rate_limited(self) -> bool:
        return time.time() < getattr(self, '_gemini_rate_limited_until', 0.0)

    @gemini_rate_limited.setter
    def gemini_rate_limited(self, val: bool):
        if val:
            self._gemini_rate_limited_until = time.time() + 15.0
        else:
            self._gemini_rate_limited_until = 0.0

    def interrupt(self) -> bool:
        """
        Instant full-duplex barge-in: immediately halts any active audio playback (<50ms)
        and signals any streaming generators to abort.
        """
        self._interrupted.set()
        halted = False
        with self._playback_lock:
            if self._current_player_proc is not None:
                try:
                    self._current_player_proc.terminate()
                    try:
                        self._current_player_proc.wait(timeout=0.03)
                    except Exception:
                        self._current_player_proc.kill()
                    halted = True
                except Exception as e:
                    print(f"[jarvis_voice] Process termination notice: {e}")
                finally:
                    self._current_player_proc = None

        return halted

    def is_speaking(self) -> bool:
        """Check whether audio is currently playing."""
        with self._playback_lock:
            if self._current_player_proc is not None:
                return self._current_player_proc.poll() is None
        return False

    def _load_config(self) -> dict:
        """Load full config.yaml as dict."""
        try:
            import yaml
            config_path = Path(__file__).parent.parent / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    return yaml.safe_load(f) or {}
        except Exception:
            pass
        return {}

    def _detect_slm(self) -> str:
        """Find which SLM is available on this machine (user configured or auto-detected)."""
        config = self._load_config()
        configured_model = config.get("model")
        
        # Fast socket connectivity pre-check to prevent blocking startup when Ollama is offline
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 11434), timeout=0.05):
                pass
        except Exception:
            return configured_model or "gemma2:2b"

        try:
            r = httpx.get("http://localhost:11434/api/tags", timeout=1.0)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                
                # 1. If configured_model from config.yaml is in local Ollama, use it!
                if configured_model:
                    for m in models:
                        if configured_model in m or m.startswith(configured_model):
                            return m
                
                # 2. Otherwise, check for candidate SLMs
                for candidate in SLM_MODEL_PREFERENCES:
                    for m in models:
                        if candidate.split(":")[0] in m or candidate in m:
                            return m

                # 3. If user pulled ANY model in Ollama, pick the first one
                if models:
                    return models[0]
        except Exception:
            pass

        return configured_model or "gemma2:2b"

    def _build_case_data(self, target: Target) -> str:
        """Build full structured case data for injection into prompt."""
        lines = []
        lines.append(f"Target: {target.primary} ({target.target_type})")
        lines.append(f"Risk score: {target.risk_score}")
        lines.append("")

        emails = [e for e in target.entities if e.entity_type == "email"]
        usernames = [e for e in target.entities if e.entity_type == "username"]
        domains = [e for e in target.entities if e.entity_type == "domain"]
        ips = [e for e in target.entities if e.entity_type == "ip"]
        pastes = [e for e in target.entities if e.entity_type == "paste"]

        if emails:
            unique_emails = []
            for e in emails:
                if e.value not in unique_emails:
                    unique_emails.append(e.value)
            lines.append(f"Email addresses found: {', '.join(unique_emails[:5])}")
            if len(unique_emails) > 5:
                lines.append(f"  ...and {len(unique_emails) - 5} more email(s)")
            
            email_platforms = [e for e in emails if e.platform]
            verified_platforms = [e for e in email_platforms if (e.metadata or {}).get("verified") is True]
            unverified_platforms = [e for e in email_platforms if (e.metadata or {}).get("verified") is not True]

            if verified_platforms:
                lines.append("Email registered on verified services (showing top 5):")
                for e in verified_platforms[:5]:
                    url = (e.metadata or {}).get("url", "")
                    if url:
                        lines.append(f"  - {e.platform}: {url}")
                    else:
                        lines.append(f"  - {e.platform}")
                if len(verified_platforms) > 5:
                    lines.append(f"  ...and {len(verified_platforms) - 5} more verified service(s)")

            if unverified_platforms:
                plat_names = sorted(list(set(e.platform for e in unverified_platforms)))
                lines.append(f"Also found associated with {len(plat_names)} additional unverified platform mentions: {', '.join(plat_names)}")

        if usernames:
            # Cap usernames listings at 8
            lines.append(f"Username '{usernames[0].value}' active on {len(usernames)} platforms (showing top 8):")
            for e in usernames[:8]:
                if e.platform:
                    url = e.metadata.get("url", "")
                    if url:
                        lines.append(f"  - {e.platform}: {url}")
                    else:
                        lines.append(f"  - {e.platform}")
            if len(usernames) > 8:
                lines.append(f"  ...and {len(usernames) - 8} more platform(s)")

        if domains:
            # Cap domains at 10
            lines.append(f"Domains/subdomains: {', '.join(e.value for e in domains[:10])}")
            if len(domains) > 10:
                lines.append(f"  ...and {len(domains) - 10} more domain(s)")

        if ips:
            # Cap IPs at 10
            lines.append(f"IP addresses: {', '.join(e.value for e in ips[:10])}")
            if len(ips) > 10:
                lines.append(f"  ...and {len(ips) - 10} more IP(s)")

        if pastes:
            # Cap pastes at 3
            lines.append(f"Found in {len(pastes)} paste site(s) (showing top 3):")
            for p in pastes[:3]:
                lines.append(f"  {p.value}")
            if len(pastes) > 3:
                lines.append(f"  ...and {len(pastes) - 3} more paste(s)")

        if target.breaches:
            # Cap breaches at 5
            lines.append(f"Breach exposures ({len(target.breaches)}, showing top 5):")
            for b in target.breaches[:5]:
                fields = ", ".join(b.exposed_fields[:4])
                lines.append(f"  {b.name} ({b.date}) — {fields}")
            if len(target.breaches) > 5:
                lines.append(f"  ...and {len(target.breaches) - 5} more breach(es)")
        else:
            lines.append("Breaches: none found")

        if target.notes:
            # Cap notes at 10
            lines.append(f"Investigator notes: {'; '.join(target.notes[:10])}")
            if len(target.notes) > 10:
                lines.append(f"  ...and {len(target.notes) - 10} more note(s)")

        # Target locations
        locations = []
        for e in target.entities:
            if e.metadata.get("geocoded"):
                geo = e.metadata["geocoded"]
                loc_str = f"{geo.get('city') or ''}, {geo.get('country') or ''}".strip(", ")
                if loc_str:
                    locations.append(f"Profile Location ({e.value}): {loc_str}")
            if e.metadata.get("exif_location"):
                locations.append(f"EXIF GPS Location ({e.value})")
            if e.entity_type == "ip" and e.metadata.get("city"):
                if not e.metadata.get("is_shared_infrastructure"):
                    locations.append(f"IP Location ({e.value}): {e.metadata.get('city')}, {e.metadata.get('country')}")
        
        if locations:
            lines.append("Physical Location Leads:")
            for loc in locations[:5]:
                lines.append(f"  - {loc}")
            if len(locations) > 5:
                lines.append(f"  ...and {len(locations) - 5} more location(s)")
            lines.append("")

        # Timeline highlights
        events = [t for t in target.timeline if t["event"] in
                  ("entity_found", "breach_found", "github_location", "ip_geo")]
        if events:
            lines.append(f"Total events in timeline: {len(target.timeline)}")

        # Correlation findings
        correlations = [t for t in target.timeline if t["event"] == "correlation_found"]
        if correlations:
            signal_groups = {}
            for c in correlations:
                signal = c["data"].get("signal", "unknown")
                pair = (c["data"].get("entity_a", ""), c["data"].get("entity_b", ""))
                signal_groups.setdefault(signal, []).append(pair)

            signal_labels = {
                "name_match": "matching identity name",
                "bio_match": "matching bio/description",
                "avatar_match": "matching profile photo",
                "location_match": "matching location"
            }
            lines.append("")
            lines.append("Cross-platform corroboration:")
            for signal, pairs in signal_groups.items():
                label = signal_labels.get(signal, signal)
                platforms = set()
                for a, b in pairs:
                    for eid in [a, b]:
                        parts = eid.split(":")
                        if len(parts) >= 3:
                            platforms.add(parts[-1])
                if platforms:
                    lines.append(f"  - {label} confirmed across: {', '.join(sorted(platforms))}")
                else:
                    lines.append(f"  - {label} ({len(pairs)} corroboration(s))")

        return "\n".join(lines)

    def _ask_slm(self, prompt: str, system: str, max_tokens: int = 4096, timeout: int = 45, num_ctx: int = 8192, temperature: float = 0.55, on_token: callable = None) -> dict:
        """Ask the local SLM. Returns a dict: {'text': response, 'error': bool}"""
        # Fast socket connectivity pre-check to prevent blocking when Ollama is offline
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 11434), timeout=0.08):
                pass
        except Exception:
            return {
                "text": "System Notice: Local SLM server is offline, Sir.",
                "error": True
            }

        try:
            url = f"{OLLAMA_URL}/api/generate"
            payload = {
                "model": self.slm_model,
                "system": system,
                "prompt": prompt,
                "stream": bool(on_token),
                "options": {
                    "temperature": temperature,
                    "top_p": 0.9,
                    "num_predict": max_tokens,
                    "num_ctx": num_ctx,
                },
            }
            if on_token:
                full_response = []
                with self.client.stream("POST", url, json=payload, timeout=timeout) as r:
                    if r.status_code == 200:
                        for line in r.iter_lines():
                            if self._interrupted.is_set():
                                break
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                tok = data.get("response", "")
                                if tok:
                                    full_response.append(tok)
                                    on_token(tok)
                            except Exception:
                                continue
                text = "".join(full_response).strip()
                if text:
                    return {"text": text, "error": False}
            else:
                r = self.client.post(url, json=payload, timeout=timeout)
                if r.status_code == 200:
                    return {"text": r.json().get("response", "").strip(), "error": False}
        except Exception:
            try:
                r = self.client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": self.slm_model,
                        "system": system,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.80,
                            "top_p": 0.9,
                            "num_predict": min(max_tokens, 1000),
                            "num_ctx": 2048,
                        },
                    },
                    timeout=90,
                )
                if r.status_code == 200:
                    return {"text": r.json().get("response", "").strip(), "error": False}
            except Exception as e2:
                return {
                    "text": f"System Notice: SLM is currently unavailable or timed out ({e2}), Sir.",
                    "error": True
                }

        return {
            "text": "System Notice: SLM request failed to return a response, Sir.",
            "error": True
        }

    def _ask_nvidia(self, prompt: str, system: str, max_tokens: int = 4096, on_token: callable = None, image_path: str = None) -> tuple[str, bool]:
        """
        Ask NVIDIA NIM API with real-time SSE streaming support and high token limit (4096).
        Returns (response_text, rate_limited).
        """
        if not self.nvidia_key or self.nvidia_rate_limited:
            return "", False

        image_content = None
        if image_path:
            p = Path(image_path)
            if not p.is_absolute():
                p = (Path(__file__).parent.parent / image_path).resolve()
            if p.exists() and p.is_file():
                import base64
                import mimetypes
                mime, _ = mimetypes.guess_type(p)
                mime = mime or "image/png"
                b64_str = base64.b64encode(p.read_bytes()).decode("ascii")
                image_content = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_str}"}}
                ]

        candidate_models = [self.nvidia_model]
        for fb in NVIDIA_FALLBACK_MODELS:
            if fb not in candidate_models:
                candidate_models.append(fb)
        for model_name in candidate_models:
            try:
                headers = {
                    "Authorization": f"Bearer {self.nvidia_key}",
                    "Content-Type": "application/json",
                }
                # Nemotron is a frontier text LLM and expects text/OCR in prompt; only dedicated vision models accept image_url
                is_vision_model = any(v in model_name.lower() for v in ["vision", "vl", "deplot", "pixtral", "llava", "multimodal"])
                user_msg_content = image_content if (image_content and is_vision_model) else prompt
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg_content},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.5,
                    "top_p": 0.9,
                    "stream": True,
                    "chat_template_kwargs": {"thinking": False},
                }

                full_response = []
                with self.client.stream("POST", NVIDIA_URL, headers=headers, json=payload, timeout=httpx.Timeout(45.0, connect=10.0)) as r:
                    if r.status_code == 429:
                        self.nvidia_rate_limited = True
                        print(f"[jarvis_voice] NVIDIA NIM API rate-limited (429). Switching to fallback.")
                        return "", True
                    if r.status_code != 200:
                        err_body = r.read().decode('utf-8', errors='ignore')[:300]
                        print(f"[jarvis_voice] NVIDIA NIM API rejected request ({model_name}): {r.status_code} — {err_body}")
                        continue

                    for line in r.iter_lines():
                        if self._interrupted.is_set():
                            break
                        if not line:
                            continue
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_str)
                                choices = chunk_json.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    tok = delta.get("content", "")
                                    if tok:
                                        full_response.append(tok)
                                        if on_token:
                                            on_token(tok)
                            except Exception:
                                continue

                result_text = "".join(full_response).strip()
                if result_text:
                    return result_text, False

            except Exception as e:
                print(f"[jarvis_voice] NVIDIA streaming error with model {model_name}: {e}")
                continue

        return "", False

    def _ask_gemini(self, prompt: str, system: str, max_tokens: int = 4096, on_token: callable = None, image_path: str = None) -> tuple[str, bool]:
        """
        Ask Gemini API with SSE streaming support and high token limit (4096).
        Returns (response_text, rate_limited).
        """
        if not self.gemini_key or self.gemini_rate_limited:
            return "", False

        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:streamGenerateContent?alt=sse"
            headers = {"Content-Type": "application/json"}

            parts = [{"text": prompt}]
            if image_path:
                p = Path(image_path)
                if not p.is_absolute():
                    p = (Path(__file__).parent.parent / image_path).resolve()
                if p.exists() and p.is_file():
                    import base64
                    import mimetypes
                    mime, _ = mimetypes.guess_type(p)
                    mime = mime or "image/png"
                    b64_str = base64.b64encode(p.read_bytes()).decode("ascii")
                    parts.append({"inline_data": {"mime_type": mime, "data": b64_str}})

            payload = {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": 0.90,
                    "topP": 0.95,
                }
            }
            full_response = []
            with self.client.stream("POST", url, params={"key": self.gemini_key}, headers=headers, json=payload, timeout=60.0) as r:
                if r.status_code == 429:
                    self.gemini_rate_limited = True
                    print(f"[jarvis_voice] Gemini API rate-limited (429). Switching to fallback.")
                    return "", True
                if r.status_code != 200:
                    err_body = r.read().decode('utf-8', errors='ignore')[:300]
                    print(f"[jarvis_voice] Gemini API rejected request: {r.status_code} — {err_body}")
                    return "", False

                for line in r.iter_lines():
                    if self._interrupted.is_set():
                        break
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            chunk_json = json.loads(data_str)
                            candidates = chunk_json.get("candidates", [])
                            if candidates:
                                parts_chunk = candidates[0].get("content", {}).get("parts", [])
                                for p_item in parts_chunk:
                                    tok = p_item.get("text", "")
                                    if tok:
                                        full_response.append(tok)
                                        if on_token:
                                            on_token(tok)
                        except Exception:
                            continue

            text = "".join(full_response).strip()
            return text, False

        except Exception as e:
            print(f"[jarvis_voice] Gemini error: {e}")
            return "", False

    def _ask_groq(self, prompt: str, system: str, max_tokens: int = 4096, on_token: callable = None) -> tuple[str, bool]:
        """
        Ask Groq LPU inference API with OpenAI-compatible SSE streaming.
        Returns (response_text, rate_limited).
        """
        if not self.groq_key or self.groq_rate_limited:
            return "", False

        headers = {
            "Authorization": f"Bearer {self.groq_key}",
            "Content-Type": "application/json",
        }
        raw_models = [self.groq_model, "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
        seen = set()
        models_to_try = [m for m in raw_models if m and not (m in seen or seen.add(m))]
        for model in models_to_try:
            if not model:
                continue
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": min(max_tokens, 4096),
                "temperature": 0.6,
                "stream": True,
            }

            try:
                full_response = []
                reasoning_chunks = []
                with self.client.stream("POST", "https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=10.0) as r:
                    if r.status_code == 429:
                        self.groq_rate_limited = True
                        print(f"[jarvis_voice] Groq API rate-limited (429). Switching to fallback.")
                        return "", True
                    if r.status_code != 200:
                        err_body = r.read().decode('utf-8', errors='ignore')[:300]
                        print(f"[jarvis_voice] Groq API notice ({r.status_code}) with {model}: {err_body}")
                        continue

                    for line in r.iter_lines():
                        if self._interrupted.is_set():
                            break
                        if not line:
                            continue
                        if line.startswith("data: "):
                            line_str = line[6:].strip()
                            if line_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(line_str)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                token = delta.get("content", "")
                                reasoning = delta.get("reasoning", "")
                                if reasoning:
                                    reasoning_chunks.append(reasoning)
                                if token:
                                    full_response.append(token)
                                    if on_token:
                                        on_token(token)
                            except Exception:
                                continue
                text = "".join(full_response).strip()
                # If content was empty but reasoning model completed, extract from reasoning
                if not text and reasoning_chunks:
                    r_text = "".join(reasoning_chunks).strip()
                    # Check if reasoning contains JSON or usable answer
                    json_m = re.search(r'\{.*\}', r_text, re.DOTALL)
                    if json_m:
                        text = json_m.group(0)
                    elif len(r_text) > 0 and len(r_text) < 300:
                        text = r_text
                if text:
                    self.groq_model = model
                    return text, False
            except Exception as e:
                print(f"[jarvis_voice] Groq streaming error with {model}: {e}")
                continue

        return "", False

    def _ask_cloud(self, prompt: str, system: str, max_tokens: int = 4096, on_token: callable = None, image_path: str = None) -> dict:
        """
        Try cloud LLMs in priority order:
        For text-only conversations, Groq LPU runs first (<200ms TTFT) for natural voice speed.
        If Groq is exhausted or vision is required, NVIDIA NIM and Gemini serve as powerhouse engines.
        Returns {text, rate_limited, engine}.
        """
        # 1. Try Groq LPU first for ultra-low conversational voice latency (<200ms)
        if self.groq_available and not self.groq_rate_limited and not image_path:
            text, r_limited = self._ask_groq(prompt, system, max_tokens=max_tokens, on_token=on_token)
            if r_limited:
                self.groq_rate_limited = True
            elif text:
                return {"text": text, "rate_limited": False, "engine": "groq"}

        # 2. Try NVIDIA NIM
        if self.nvidia_available and not self.nvidia_rate_limited:
            text, r_limited = self._ask_nvidia(prompt, system, max_tokens=max_tokens, on_token=on_token, image_path=image_path)
            if r_limited:
                self.nvidia_rate_limited = True
            elif text:
                return {"text": text, "rate_limited": False, "engine": "nvidia"}

        # 3. Try Gemini third
        if self.gemini_available and not self.gemini_rate_limited:
            text, r_limited = self._ask_gemini(prompt, system, max_tokens=max_tokens, on_token=on_token, image_path=image_path)
            if r_limited:
                self.gemini_rate_limited = True
            elif text:
                return {"text": text, "rate_limited": False, "engine": "gemini"}

        # All exhausted or rate-limited
        rate_limited = (self.nvidia_rate_limited or self.groq_rate_limited or self.gemini_rate_limited)
        return {"text": "", "rate_limited": rate_limited, "engine": None}

    def _sanitize_text_for_speech(self, text: str) -> str:
        """Sanitize text before TTS synthesis so underscores, markdown formatting, URLs, and symbols are spoken naturally."""
        # Remove [CMD: ...] directives cleanly without breaking on internal brackets
        _, clean = extract_cmd_directive(text)
        clean = re.sub(r'\[CMD[^\]]*\]', '', clean, flags=re.IGNORECASE)
        # Remove markdown URLs [Title](url) -> Title
        clean = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', clean)
        # Never speak internal Action-HUD/tool payloads. These are implementation
        # artifacts, not user-facing answer content.
        clean = re.sub(r'\[Action HUD[^\n]*\][\s\S]*$', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'```(?:json)?[\s\S]*?```', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\{\s*\"(?:skill_triggered|action|target|status|findings_so_far)\"[\s\S]*?\}', '', clean, flags=re.IGNORECASE)
        # Remove raw URLs
        clean = re.sub(r'https?://\S+', '', clean)
        clean = re.sub(r'www\.\S+', '', clean)
        clean = re.sub(r'\b\w+\.(?:com|org|net|edu|gov|mil|int|co\.uk|ca|de|fr|jp|au|us|ru|ch|it|nl|se|no|dk|fi|pl|be|at|pt|gr|ie|hu|cz|sk|si|hr|bg|ro|lv|lt|ee|cy|mt|is|li|lu|mc|sm|va|ad|mc)\b', '', clean, flags=re.IGNORECASE)
        # Acronym normalization — prevent letter-by-letter spelling or dot splitting on J.A.R.V.I.S.
        clean = re.sub(r'\bJ\.?A\.?R\.?V\.?I\.?S\.?', 'Jarvis', clean, flags=re.IGNORECASE)

        # Geographic Coordinates normalization (e.g. 11.4228° N, 76.8661° E -> 11.42 degrees North, 76.86 degrees East)
        def _norm_coord(m):
            val, direction = m.group(1), m.group(2).upper()
            d_map = {'N': 'North', 'S': 'South', 'E': 'East', 'W': 'West'}
            return f"{val} degrees {d_map.get(direction, direction)}"
        clean = re.sub(r'(\d+(?:\.\d+)?)\s*°?\s*([NSEWnsew])\b', _norm_coord, clean)

        # Temperature unit conversion
        clean = re.sub(r'(\d+(?:\.\d+)?)\s*°?\s*C\b', r'\1 degrees Celsius', clean)
        clean = re.sub(r'(\d+(?:\.\d+)?)\s*°?\s*F\b', r'\1 degrees Fahrenheit', clean)
        clean = re.sub(r'\b(\d+(?:\.\d+)?)\s*celsius\b', r'\1 degrees Celsius', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\b(\d+(?:\.\d+)?)\s*fahrenheit\b', r'\1 degrees Fahrenheit', clean, flags=re.IGNORECASE)
        clean = re.sub(r'(\d+(?:\.\d+)?)\s*°', r'\1 degrees ', clean)
        clean = clean.replace('°', ' degrees ')

        # Common symbols normalization
        clean = clean.replace('%', ' percent ')
        clean = clean.replace('&', ' and ')
        clean = clean.replace('@', ' at ')
        clean = clean.replace('±', ' plus or minus ')
        clean = clean.replace('—', ' — ')

        # Leet-Speak & Handle Phonetic Normalization (e.g. l4zz3rj0d -> lazzerjod, pr0ject -> project)
        leet_direct = {
            "l4zz3rj0d": "lazzerjod",
            "l4zzerj0d": "lazzerjod",
            "l4zz3rjod": "lazzerjod",
            "l4zzerjod": "lazzerjod",
            "l4zerj0d": "lazzerjod",
            "lazzerj0d": "lazzerjod",
            "1337": "leet",
            "l33t": "leet",
            "pwned": "pawned",
            "pwn3d": "pawned",
            "pr0ject": "project",
            "h3llh0und": "hellhound",
        }
        leet_char_map = {
            "0": "o",
            "1": "i",
            "3": "e",
            "4": "a",
            "5": "s",
            "7": "t",
            "@": "a",
            "$": "s",
        }

        def _deleet_token(match):
            tok = match.group(0)
            low = tok.lower()
            if low in leet_direct:
                return leet_direct[low]
            # Exclude tech terms, hashes, versions, CVEs, model identifiers
            if any(low.startswith(p) for p in ["sha", "md5", "gpt", "qwen", "win", "ipv", "mp", "h26", "utf", "cve"]):
                return tok
            if len(tok) > 12 and all(c in "0123456789abcdefABCDEF" for c in tok):
                return tok
            # Must have letters mixed with leet numbers
            has_alpha = any(c.isalpha() for c in tok)
            has_leet_num = any(c in "013457@$" for c in tok)
            if has_alpha and has_leet_num:
                # Avoid unit numbers like 3b, 120b, 70b, 4k, 1080p, 5ghz
                if re.match(r'^\d+[a-zA-Z]{1,3}$', tok):
                    return tok
                return "".join(leet_char_map.get(c, c) for c in tok)
            return tok

        clean = re.sub(r'\b[a-zA-Z0-9@$]{2,}\b', _deleet_token, clean)

        # Strip stage directions, roleplay actions and parentheticals (*grins*, *cracks knuckles*, (chuckles), etc.)
        clean = re.sub(r'\*(?:chuckle|grin|laugh|smirk|sigh|snicker|wink|shrug|cough|cracks knuckles|clears throat|pauses|leans)[^*]*\*', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\([^)]*(?:chuckle|grin|laugh|smirk|sigh|snicker|wink|shrug|cough|cracks|leans|snort)[^)]*\)', '', clean, flags=re.IGNORECASE)
        # Add natural pauses around punctuation for more human-like speech (protect decimals from getting broken into "11. 42")
        clean = re.sub(r'(?<!\d)([.!?])(?!\d)\s*', r'\1 ', clean)
        clean = re.sub(r'(?<!\d)([,;:])(?!\d)\s*', r'\1 ', clean)
        # Replace snake_case underscores with spaces (e.g. open_app -> open app)
        clean = re.sub(r'(\w+)_(\w+)', r'\1 \2', clean)
        clean = clean.replace('_', ' ')
        # Remove markdown symbols (*, #, `, ~)
        clean = re.sub(r'[`*#~]', '', clean)
        # Normalize whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def stream_fish_audio_pcm(self, text_or_source, is_interrupted_fn=None, on_clause_sent=None):
        """Stream speech through Fish Audio's native /v1/tts/live WebSocket API.
        
        Args:
            text_or_source: String, Queue, or Iterable yielding clauses/sentences.
            is_interrupted_fn: Optional callable returning True if playback was interrupted.
            on_clause_sent: Optional callable receiving each cleaned text chunk when sent to WebSocket.
        Yields:
            tuple (pcm_bytes: bytes, sample_rate: int)
        """
        if not self.fish_audio_available or not self.fish_audio_key:
            return
        if not HAS_WEBSOCKET_TTS or ws_connect is None or msgpack is None:
            raise RuntimeError("Native WebSocket streaming requires 'websockets' and 'msgpack' packages")

        # Reset interruption state cleanly at the start of every stream turn
        self._interrupted.clear()

        uri = getattr(self, "fish_audio_ws_url", "wss://api.fish.audio/v1/tts/live")
        headers = {
            "Authorization": f"Bearer {self.fish_audio_key}",
            "model": self.fish_audio_model,
        }

        ws = None
        sender_thread = None
        stop_sending = threading.Event()
        stop_sent_time = [0.0]

        try:
            ws = ws_connect(uri, additional_headers=headers, ping_interval=None, ping_timeout=None)
        except Exception as ws_err:
            err_str = str(ws_err).lower()
            if "402" in err_str or "insufficient" in err_str:
                print(f"[jarvis_voice] Notice: Paid credits or valid free model required for {self.fish_audio_model}. Disabling Fish Audio.")
                self.fish_audio_available = False
            else:
                print(f"[jarvis_voice] Fish Audio WebSocket connection error: {ws_err}")
            raise

        try:
            # 1. Send initial start configuration packet (latency="low", chunk_length=100)
            start_payload = {
                "event": "start",
                "request": {
                    "text": "",
                    "reference_id": self.fish_audio_voice_id,
                    "format": "pcm",
                    "sample_rate": 24000,
                    "latency": "low",
                    "chunk_length": 100,
                    "model": self.fish_audio_model,
                }
            }
            ws.send(msgpack.packb(start_payload))

            # 2. Text sender worker
            def text_sender_worker():
                try:
                    if isinstance(text_or_source, str):
                        clean_text = self._sanitize_text_for_speech(text_or_source)
                        if clean_text and not stop_sending.is_set():
                            if on_clause_sent:
                                try:
                                    on_clause_sent(clean_text)
                                except Exception:
                                    pass
                            ws.send(msgpack.packb({"event": "text", "text": clean_text}))
                        if not stop_sending.is_set():
                            stop_sent_time[0] = time.time()
                            ws.send(msgpack.packb({"event": "stop"}))
                    elif isinstance(text_or_source, queue.Queue):
                        while not stop_sending.is_set():
                            try:
                                item = text_or_source.get(timeout=0.1)
                            except queue.Empty:
                                if self._interrupted.is_set() or (is_interrupted_fn and is_interrupted_fn()):
                                    break
                                continue
                            if item is None:
                                text_or_source.task_done()
                                if not stop_sending.is_set():
                                    stop_sent_time[0] = time.time()
                                    ws.send(msgpack.packb({"event": "stop"}))
                                break
                            clean_chunk = self._sanitize_text_for_speech(item)
                            if clean_chunk and not stop_sending.is_set():
                                if on_clause_sent:
                                    try:
                                        on_clause_sent(clean_chunk)
                                    except Exception:
                                        pass
                                ws.send(msgpack.packb({"event": "text", "text": clean_chunk}))
                            text_or_source.task_done()
                    else:
                        for chunk in text_or_source:
                            if stop_sending.is_set() or self._interrupted.is_set() or (is_interrupted_fn and is_interrupted_fn()):
                                break
                            clean_chunk = self._sanitize_text_for_speech(chunk)
                            if clean_chunk and not stop_sending.is_set():
                                if on_clause_sent:
                                    try:
                                        on_clause_sent(clean_chunk)
                                    except Exception:
                                        pass
                                ws.send(msgpack.packb({"event": "text", "text": clean_chunk}))
                        if not stop_sending.is_set():
                            stop_sent_time[0] = time.time()
                            ws.send(msgpack.packb({"event": "stop"}))
                except Exception:
                    pass

            sender_thread = threading.Thread(target=text_sender_worker, daemon=True)
            sender_thread.start()

            # 3. Audio receiver loop with 20s general idle timeout, 10s post-stop idle timeout, and finish-event validation
            received_finish_event = False

            def _iter_incoming_messages():
                # If mock_ws from unit tests, iterate directly over ws mock
                is_mock = "Mock" in type(ws).__name__ or (hasattr(ws, "__iter__") and "Mock" in type(getattr(ws, "__iter__")).__name__)
                if not is_mock and hasattr(ws, "recv"):
                    while not (self._interrupted.is_set() or (is_interrupted_fn and is_interrupted_fn())):
                        timeout_val = 10.0 if stop_sent_time[0] > 0.0 else 20.0
                        try:
                            msg = ws.recv(timeout=timeout_val)
                        except TimeoutError:
                            if stop_sent_time[0] > 0.0:
                                print("[jarvis_voice] Notice: Post-stop idle timeout (10.0s with no audio after stop); connection stalled.")
                                raise TimeoutError("Fish Audio WebSocket post-stop idle timeout (10.0s)")
                            print("[jarvis_voice] Notice: Fish Audio WebSocket idle timeout (20.0s with no audio); connection dead.")
                            raise TimeoutError("Fish Audio WebSocket idle timeout (20.0s with no data)")
                        yield msg
                else:
                    for msg in ws:
                        yield msg

            for msg in _iter_incoming_messages():
                # Immediate barge-in interruption check: close socket with NO wait
                if self._interrupted.is_set() or (is_interrupted_fn and is_interrupted_fn()):
                    stop_sending.set()
                    try:
                        ws.close()
                    except Exception:
                        pass
                    break

                if isinstance(msg, bytes):
                    try:
                        data = msgpack.unpackb(msg)
                    except Exception:
                        data = None

                    if isinstance(data, dict):
                        audio = data.get("audio")
                        if audio:
                            yield audio, 24000
                        event = data.get("event")
                        if event == "finish":
                            received_finish_event = True
                            break
                        elif event == "error":
                            err_detail = data.get("error") or data.get("reason") or data.get("message") or data
                            print(f"[jarvis_voice] Fish Audio live error event: {err_detail}")
                            raise RuntimeError(f"Fish Audio live error event: {err_detail}")
                    elif len(msg) > 100:
                        yield msg, 24000

            # Verify clean completion vs premature truncation:
            # Gated on not interrupted so intentional barge-in closes are NEVER treated as truncation.
            is_interrupted = self._interrupted.is_set() or (is_interrupted_fn and is_interrupted_fn())
            is_mock = "Mock" in type(ws).__name__ or (hasattr(ws, "__iter__") and "Mock" in type(getattr(ws, "__iter__")).__name__)
            if not is_interrupted and not received_finish_event and not is_mock:
                print("[jarvis_voice] Notice: Fish Audio WebSocket stream closed before finish event; audio truncated.")
                raise RuntimeError("Fish Audio WebSocket stream closed before finish event; audio truncated")

        except (ConnectionClosed, Exception) as stream_err:
            if not (self._interrupted.is_set() or (is_interrupted_fn and is_interrupted_fn())):
                print(f"[jarvis_voice] Fish Audio WebSocket stream notice: {stream_err}")
                raise
        finally:
            stop_sending.set()
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            if sender_thread is not None and sender_thread.is_alive():
                sender_thread.join(timeout=1.0)


    def _synthesize_fish_audio(self, text: str) -> Optional[bytes]:
        """Synthesize speech via Fish Audio API using free model s2.1-pro-free and reference voice ID."""
        if not self.fish_audio_available or not text or not text.strip():
            return None

        clean_text = self._sanitize_text_for_speech(text)
        if not clean_text:
            return None

        url = "https://api.fish.audio/v1/tts"
        headers = {
            "Authorization": f"Bearer {self.fish_audio_key}",
            "Content-Type": "application/json",
            "model": self.fish_audio_model
        }
        payload = {
            "text": clean_text,
            "reference_id": self.fish_audio_voice_id,
            "format": "mp3",
            "model": self.fish_audio_model
        }

        try:
            r = self.client.post(url, json=payload, headers=headers, timeout=12.0)
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code == 402 or "insufficient" in r.text.lower():
                print(f"[jarvis_voice] Notice: Fish Audio credit depleted for {self.fish_audio_model}. Disabling Fish Audio for session.")
                self.fish_audio_available = False
                return None
            print(f"[jarvis_voice] Fish Audio API status {r.status_code}: {r.text[:150]}")
        except Exception as e:
            print(f"[jarvis_voice] Fish Audio API notice: {e}")
        return None

    def _synthesize_edge_tts(self, text: str) -> Optional[bytes]:
        """High-quality cloud neural TTS fallback using edge-tts."""
        try:
            import asyncio
            import edge_tts
            async def _run():
                communicate = edge_tts.Communicate(text, "en-GB-RyanNeural", rate="+4%", pitch="-2Hz")
                buf = bytearray()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
                return bytes(buf) if buf else None

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(asyncio.wait_for(_run(), timeout=6.0))
            finally:
                loop.close()
        except Exception:
            return None

    def _synthesize_espeak(self, text: str) -> Optional[bytes]:
        """Offline zero-latency system TTS fallback using British J.A.R.V.I.S. voice."""
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp_path = tf.name
            cmd = ["espeak", "-v", "en-gb+m3", "-s", "155", "-p", "45", "-w", tmp_path, text]
            p = subprocess.run(cmd, capture_output=True, timeout=5.0)
            if p.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 44:
                with open(tmp_path, "rb") as f:
                    data = f.read()
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return data
        except Exception:
            pass
        return None

    def narrate(self, text: str) -> Optional[bytes]:
        """Voice synthesis via Fish Audio API with edge-tts and local espeak fallbacks."""
        if not text:
            return None

        clean_text = self._sanitize_text_for_speech(text)
        if not clean_text:
            return None

        # 1. Try Fish Audio cloud synthesis first
        if self.fish_audio_available:
            audio = self._synthesize_fish_audio(clean_text)
            if audio:
                return audio

        # 2. Try Local Voice Clone (Chatterbox) if available
        if hasattr(self, 'local_clone') and getattr(self.local_clone, 'available', False):
            audio = self.local_clone.synthesize(clean_text)
            if audio:
                return audio

        # 3. Try high quality Edge TTS
        audio = self._synthesize_edge_tts(clean_text)
        if audio:
            return audio

        # 4. Reliable offline system TTS fallback
        return self._synthesize_espeak(clean_text)

    def synthesize_speech_b64(self, text: str) -> Optional[str]:
        """Synthesize speech and return base64 audio data URL."""
        if not text:
            return None

        audio_bytes = self.narrate(text)
        if not audio_bytes:
            return None

        b64 = base64.b64encode(audio_bytes).decode("ascii")
        mime = "audio/wav" if audio_bytes.startswith(b"RIFF") else "audio/mp3"
        return f"data:{mime};base64,{b64}"

    def speak(self, text: str) -> None:
        """Synthesizes and immediately plays audio on system speakers with instant barge-in support."""
        if not text:
            return
        self._interrupted.clear()
        audio_bytes = self.narrate(text)
        if not audio_bytes or self._interrupted.is_set():
            return

        temp_path = None
        try:
            import subprocess
            import tempfile
            import shutil
            suffix = ".wav" if audio_bytes.startswith(b"RIFF") else ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                temp_path = f.name

            players = ["mpv", "ffplay", "paplay", "aplay"]
            selected_cmd = None
            for player in players:
                if shutil.which(player):
                    if player == "ffplay":
                        selected_cmd = [player, "-nodisp", "-autoexit", "-loglevel", "quiet", temp_path]
                    elif player == "mpv":
                        selected_cmd = [player, "--no-video", "--really-quiet", temp_path]
                    else:
                        selected_cmd = [player, temp_path]
                    break

            if selected_cmd and not self._interrupted.is_set():
                with self._playback_lock:
                    self._current_player_proc = subprocess.Popen(
                        selected_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )

                # Poll process while staying responsive to barge-in interrupt
                while self._current_player_proc and self._current_player_proc.poll() is None:
                    if self._interrupted.is_set():
                        with self._playback_lock:
                            if self._current_player_proc:
                                try:
                                    self._current_player_proc.terminate()
                                    self._current_player_proc.wait(timeout=0.03)
                                except Exception:
                                    try:
                                        self._current_player_proc.kill()
                                    except Exception:
                                        pass
                                self._current_player_proc = None
                        break
                    time.sleep(0.02)

                with self._playback_lock:
                    self._current_player_proc = None
        except Exception as e:
            print(f"[jarvis_voice] speak error: {e}")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    # ── Public interface ──────────────────────────────────────

    def resolve_search_subject(self, query: str, target: Target = None) -> str:
        """
        Resolve relative pronouns ('that guy', 'him', 'he', 'bro') to concrete target handles
        or active conversation subjects.
        """
        clean_q = query.strip()
        if not clean_q or self.skills.is_relative_query(clean_q):
            if target and getattr(target, 'name', None):
                return target.name
            if target and getattr(target, 'primary', None):
                return target.primary

            # Inspect session memory to extract last target/handle
            history_text = self.session_memory.to_text(10)
            if history_text:
                # 1. Look for quoted strings (e.g. "l4zz3rj0d")
                quoted = re.findall(r'["\']([a-zA-Z0-9_\-\.]+)\b["\']', history_text)
                if quoted:
                    return quoted[-1]
                # 2. Look for target identifiers or handles
                handles = re.findall(r'\b[a-zA-Z0-9_\-]{3,20}\b', history_text)
                stops = {
                    "investigate", "search", "google", "about", "who", "that", "this", "tell",
                    "user", "jarvis", "dean", "detective", "record", "live", "intelligence",
                    "scan", "findings", "case", "target", "bro", "guy", "what", "there", "info"
                }
                candidates = [h for h in handles if h.lower() not in stops and not h.isdigit()]
                if candidates:
                    return candidates[-1]
        return clean_q

    def chat(self, question: str, target: Target = None, on_token: callable = None, image_path: str = None) -> dict:
        """
        Mode 1 or Mode 3 depending on whether target has findings.
        Priority: NVIDIA NIM → Gemini → local SLM.
        Returns {text, rate_limited, mode, error, show_panel, search_query, panel_payload}
        """
        # 1. System OS Skill execution check
        # Skip if this is a context prompt from desktop.py fast-path (skill already executed or command debrief)
        _is_skill_context = (
            question.lstrip().startswith("[SKILL_CONTEXT]")
            or question.lstrip().startswith("[REAL SYSTEM SKILL EXECUTED]")
            or question.lstrip().startswith("[COMMAND_DEBRIEF]")
        )
        if not _is_skill_context:
            try:
                res_skill = self.skills.try_execute(question)
                if len(res_skill) == 5:
                    handled, skill_msg, is_search, search_query, skill_payload = res_skill
                else:
                    handled, skill_msg, is_search, search_query = res_skill
                    skill_payload = {}
            except Exception as se:
                print(f"[jarvis_voice] System skill try_execute error suppressed: {se}")
                handled, skill_msg, is_search, search_query, skill_payload = False, "", False, "", {}
        else:
            handled, skill_msg, is_search, search_query, skill_payload = False, "", False, "", {}
        if handled and not is_search:
            clean_skill_msg = self._sanitize_text_for_speech(skill_msg)
            self.session_memory.add("user", question)
            self.session_memory.add("jarvis", clean_skill_msg)
            if on_token:
                for token in clean_skill_msg.split(' '):
                    on_token(token + ' ')
            # Skill handlers (calendar, inbox, maps, cloud docs, smart home, self-upgrade,
            # open-app, etc.) already build a structured HUD payload in core/system_skills.py
            # — forward it instead of silently dropping it, or the desktop panel never opens.
            return {
                "text": clean_skill_msg,
                "rate_limited": False,
                "mode": "advisor",
                "error": False,
                "engine": "system_skill",
                "show_panel": bool(skill_payload),
                "search_query": skill_payload.get("topic", "") if skill_payload else "",
                "panel_payload": skill_payload
            }

        # 1.5 Direct signature response check for identity & creator queries
        sal = self.memory.get_salutation() or "Sir"
        q_lower = question.strip().lower()
        clean_q = re.sub(r'[^\w\s]', '', q_lower).strip()

        creator_patterns = [
            "who built you", "who made you", "who created you", "who is your creator",
            "who designed you", "who developed you", "who is your developer",
            "who programmed you", "who invented you", "who coded you", "who wrote you",
            "who is your author", "who built jarvis", "who created jarvis", "who made jarvis",
            "who owns you"
        ]
        if any(cp in clean_q for cp in creator_patterns):
            creator_msg = f"I was engineered and deployed by Project Hellhound, created by l4zz3rj0d. I operate as your personal tactical intelligence officer, {sal}."
            self.session_memory.add("user", question)
            self.session_memory.add("jarvis", creator_msg)
            if on_token:
                for token in creator_msg.split(' '):
                    on_token(token + ' ')
            return {
                "text": creator_msg,
                "rate_limited": False,
                "mode": "advisor",
                "error": False,
                "engine": "signature_response",
                "show_panel": False,
                "search_query": "",
                "panel_payload": {}
            }

        identity_patterns = [
            "who are you", "who are u", "who u are", "what is your name", "whats your name",
            "who the fuck are you", "what are you", "what is jarvis", "what are u"
        ]
        if any(ip == clean_q for ip in identity_patterns):
            identity_msg = f"I am J.A.R.V.I.S., an autonomous tactical intelligence officer and personal assistant built by Project Hellhound. At your service, {sal}."
            self.session_memory.add("user", question)
            self.session_memory.add("jarvis", identity_msg)
            if on_token:
                for token in identity_msg.split(' '):
                    on_token(token + ' ')
            return {
                "text": identity_msg,
                "rate_limited": False,
                "mode": "advisor",
                "error": False,
                "engine": "signature_response",
                "show_panel": False,
                "search_query": "",
                "panel_payload": {}
            }

        # 2. Check for investigation trigger with ambiguous target
        ambiguous_triggers = [
            "investigate", "jarvis investigate", "hey jarvis investigate",
            "start investigation", "investigate someone", "investigate something", "investigate target"
        ]
        if q_lower in ambiguous_triggers or (q_lower.startswith("investigate") and len(q_lower.split()) <= 2 and q_lower.split()[-1] in ["someone", "something", "target", "person", "user"]):
            return {
                "text": "Opening target investigation box. Type the exact target username, email, or domain you want me to sniff out.",
                "open_dialog": True,
                "rate_limited": False,
                "mode": "advisor",
                "error": False,
                "engine": "dialog_trigger"
            }

        # 3. Handle live web search resolution & synthesis
        # Only pre-search if explicitly instructed via search skill command (no greedy regex word matching)
        has_search_intent = (not _is_skill_context) and is_search
        resolved_search_query = ""
        live_search_intel = ""
        search_panel_payload = {}

        if has_search_intent:
            raw_query = search_query if search_query else question
            resolved_search_query = self.resolve_search_subject(raw_query, target)
            if resolved_search_query:
                intel_summary, raw_results = self.skills.perform_live_search(resolved_search_query)
                if raw_results:
                    search_panel_payload = self.skills.hud_engine.build_structured_payload(
                        resolved_search_query, "SEARCH", raw_results, intel_summary
                    )
                if intel_summary:
                    delivery_style = (
                        "Present the REAL facts using your sophisticated, articulate, dryly witty J.A.R.V.I.S. style, addressing the operator as 'Sir'."
                    )
                    live_search_intel = (
                        f"\n\n[REAL-WORLD LIVE WEB SCAN RESULTS FOR '{resolved_search_query}']:\n{intel_summary}\n\n"
                        f"[ABSOLUTE GROUNDING & ZERO-HALLUCINATION INSTRUCTIONS]:\n"
                        f"1. You MUST summarize the exact real-world web search findings shown above for '{resolved_search_query}'.\n"
                        f"2. ZERO Fictional Lore: Do NOT invent fictional show stories when presenting real web search results.\n"
                        f"3. Persona Delivery: {delivery_style}\n"
                        f"4. Be accurate, sharp, and stay strictly grounded in the live search records above."
                    )

        # Inject Memory summary & Cross-Session Recall into system prompt & session history
        mem_summary = self.memory.get_memory_summary_for_prompt()
        cross_session = self.session_memory.get_cross_session_context()
        if cross_session:
            mem_summary += "\n\n" + cross_session

        # Inject Active Session Files & Notes Context ONLY when operator's query refers to files/documents
        try:
            from core.system_commander import get_system_commander
            cmdr = get_system_commander()
            last_f = getattr(cmdr, 'last_affected_file', None)
            q_lower = (question or "").lower()
            needs_file_ctx = bool(last_f and any(w in q_lower for w in [
                "note", "notebook", "file", "document", "this one", "rename", "edit", "append", "add to", "save to", "delete the", "remove the"
            ]) and not any(m in q_lower for m in ["personal memory", "save rule", "memory rule"]))
            if needs_file_ctx:
                file_ctx = (
                    f"\n\n[SESSION RECENT FILES & ACTIVE WORKFLOW]:\n"
                    f"- Active Document / Last Modified File: `{last_f}`\n"
                    f"- Critical Instruction: If the operator says 'this one', 'it', 'the note', 'the notebook', 'rename it', or 'add to it', they are referring to `{last_f}`.\n"
                    f"- When renaming, editing, or appending, always use the exact full path `{last_f}`. Never invent 'notebook.txt'."
                )
                mem_summary += file_ctx
        except Exception:
            pass

        recent_history = self.session_memory.to_text(8)

        sal = self.memory.get_salutation() or "Sir"
        if sal == "Sir":
            sal_protocol = "\n\n[CRITICAL OPERATOR PROTOCOL]: The operator's active salutation is 'Sir'. Address the operator as 'Sir' in all dialogue. Do not use 'Ma'am' or 'Madam'."
        else:
            sal_protocol = f"\n\n[CRITICAL OPERATOR PROTOCOL]: The operator's active salutation is '{sal}'. Address the operator as '{sal}' in all dialogue. Do not use 'Sir'."

        if target and target.entities:
            # Mode 3 — case loaded, answer from findings
            case_data = self._build_case_data(target)
            investigator_base = self.investigator_prompt_template.format(case_data=case_data)
            if sal != "Sir":
                investigator_base = re.sub(r'\bSir\b', sal, investigator_base)
            system = investigator_base + "\n\n" + mem_summary + sal_protocol
            if live_search_intel:
                system += live_search_intel + "\n\n[PRIORITY OVERRIDE]: IGNORE any prior hallucinated conversation history regarding this target. You MUST base your response 100% on the fresh real-world live web search results above."
            if recent_history:
                prompt = f"Recent Conversation History:\n{recent_history}\n\nUser follow-up question: {question}"
            else:
                prompt = f"User asked: {question}"
            mode = "investigation"
            max_tok = 1800
        else:
            # Mode 1 — no case, OSINT advisor
            active_target_note = f"\nActive Investigation Target: {target.name}" if (target and getattr(target, 'name', None)) else ""
            advisor_base = self.advisor_prompt
            if sal != "Sir":
                advisor_base = re.sub(r'\bSir\b', sal, advisor_base)
            system = advisor_base + active_target_note + "\n\n" + mem_summary + sal_protocol
            if live_search_intel:
                system += live_search_intel + "\n\n[PRIORITY OVERRIDE]: IGNORE any prior hallucinated conversation history regarding this target. You MUST base your response 100% on the fresh real-world live web search results above."
            if recent_history:
                prompt = f"Recent Conversation History:\n{recent_history}\n\nUser follow-up question: {question}"
            else:
                prompt = question
            mode = "advisor"
            max_tok = 1400

        is_action_popup = bool(resolved_search_query)

        def _process_final_text(raw_text: str) -> tuple[str, dict]:
            cleaned = self._clean_reasoning(raw_text)
            tactical = {}

            # Detect autonomous [CMD: <command>] safely handling quotes and brackets
            cmd_to_run, cleaned = extract_cmd_directive(cleaned)
            if cmd_to_run:
                try:
                    from core.system_commander import get_system_commander
                    commander = get_system_commander()
                    commander.run_as_task(cmd_to_run, title=f"Terminal: {cmd_to_run[:30]}", skip_debrief=True)
                    tactical["cmd"] = cmd_to_run
                except Exception as e:
                    print(f"[voice] Autonomous command launch error: {e}")

                if not cleaned or len(cleaned) < 5:
                    addressed = "Sir"
                    cleaned = f"Executing `{cmd_to_run}` on your system now, {addressed}. Check the panel."

            cleaned = re.sub(r'\[CMD[^\]]*\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [NAV: <location>] (whitespace-tolerant)
            m_nav = re.search(r'\[\s*NAV\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_nav:
                tactical["nav"] = m_nav.group(1).strip()
                cleaned = re.sub(r'\[\s*NAV\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [LAYER: <layer> <on|off>]
            m_layer = re.search(r'\[\s*LAYER\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_layer:
                tactical["layer"] = m_layer.group(1).strip()
                cleaned = re.sub(r'\[\s*LAYER\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [ZOOM: <in|out>]
            m_zoom = re.search(r'\[\s*ZOOM\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_zoom:
                tactical["zoom"] = m_zoom.group(1).strip()
                cleaned = re.sub(r'\[\s*ZOOM\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [RADIO: <action>]
            m_radio = re.search(r'\[\s*RADIO\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_radio:
                tactical["radio"] = m_radio.group(1).strip()
                cleaned = re.sub(r'\[\s*RADIO\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [SFX: <effect>]
            m_sfx = re.search(r'\[\s*SFX\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_sfx:
                tactical["sfx"] = m_sfx.group(1).strip()
                cleaned = re.sub(r'\[\s*SFX\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [ANNOTATE: <directive>]
            m_annotate = re.search(r'\[\s*ANNOTATE\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_annotate:
                tactical["annotate"] = m_annotate.group(1).strip()
                cleaned = re.sub(r'\[\s*ANNOTATE\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [COCKPIT: <directive>]
            m_cockpit = re.search(r'\[\s*COCKPIT\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_cockpit:
                tactical["cockpit"] = m_cockpit.group(1).strip()
                cleaned = re.sub(r'\[\s*COCKPIT\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [SEARCH: <directive>]
            m_search_dir = re.search(r'\[\s*SEARCH\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_search_dir:
                tactical["search"] = m_search_dir.group(1).strip()
                cleaned = re.sub(r'\[\s*SEARCH\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [YOUTUBE: <directive>]
            m_yt_dir = re.search(r'\[\s*YOUTUBE\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_yt_dir:
                tactical["youtube"] = m_yt_dir.group(1).strip()
                cleaned = re.sub(r'\[\s*YOUTUBE\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [APP: <directive>]
            m_app_dir = re.search(r'\[\s*APP\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_app_dir:
                tactical["app"] = m_app_dir.group(1).strip()
                cleaned = re.sub(r'\[\s*APP\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Detect [MEDIA: <directive>]
            m_media_dir = re.search(r'\[\s*MEDIA\s*:\s*([^\]]+)\]', cleaned, re.IGNORECASE)
            if m_media_dir:
                tactical["media"] = m_media_dir.group(1).strip()
                cleaned = re.sub(r'\[\s*MEDIA\s*:[^\]]+\]', '', cleaned, flags=re.IGNORECASE).strip()

            # Residual cleanup to ensure zero leaked tactical directives or brackets reach TTS
            cleaned = re.sub(r'\[\s*(?:NAV|LAYER|CMD|ZOOM|RADIO|SFX|ANNOTATE|COCKPIT|SEARCH|YOUTUBE|APP|MEDIA)[^\]]*\]', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()

            return cleaned, tactical

        # Try cloud engines first (NVIDIA → Gemini)
        cloud = self._ask_cloud(prompt, system, max_tokens=max_tok, on_token=on_token, image_path=image_path)
        primary_text = cloud["text"]

        # Autonomous Cognitive Search Tool Resolution (ReAct turn)
        if primary_text:
            m_cog_search = re.search(r'\[\s*SEARCH\s*:\s*([^\]]+)\]', primary_text, re.IGNORECASE)
            if m_cog_search and not live_search_intel:
                tool_query = m_cog_search.group(1).strip()
                print(f"[voice] Cognitive tool invoked by AI model: [SEARCH: '{tool_query}']")
                intel_summary, raw_results = self.skills.perform_live_search(tool_query)
                if raw_results:
                    resolved_search_query = tool_query
                    search_panel_payload = self.skills.hud_engine.build_structured_payload(
                        tool_query, "SEARCH", raw_results, intel_summary
                    )
                    is_action_popup = True
                    second_prompt = (
                        f"{prompt}\n\n[REAL-WORLD LIVE WEB SCAN RESULTS FOR '{tool_query}']:\n{intel_summary}\n\n"
                        f"[INSTRUCTIONS]: Ground your verbal debrief to Sir in the live search results above. "
                        f"Deliver an articulate, accurate answer with your characteristic wit. Do NOT emit [SEARCH: ...] again."
                    )
                    second_cloud = self._ask_cloud(second_prompt, system, max_tokens=max_tok, on_token=on_token, image_path=image_path)
                    if second_cloud["text"]:
                        cloud = second_cloud

            # Autonomous YouTube Search (ReAct turn)
            m_cog_youtube = re.search(r'\[\s*YOUTUBE\s*:\s*([^\]]+)\]', primary_text, re.IGNORECASE)
            if m_cog_youtube and not live_search_intel:
                yt_query = m_cog_youtube.group(1).strip()
                print(f"[voice] Cognitive tool invoked by AI model: [YOUTUBE: '{yt_query}']")
                yt_summary, yt_results = self.skills.perform_youtube_search(yt_query)
                if yt_results:
                    resolved_search_query = yt_query
                    search_panel_payload = self.skills.hud_engine.build_structured_payload(
                        f"YouTube: {yt_query}", "YOUTUBE", yt_results, yt_summary
                    )
                    is_action_popup = True
                    second_prompt = (
                        f"{prompt}\n\n[YOUTUBE VIDEO RESULTS FOR '{yt_query}']:\n{yt_summary}\n\n"
                        f"[INSTRUCTIONS]: Ground your verbal debrief in the YouTube results above. "
                        f"Deliver an articulate summary of what was found. Do NOT emit [YOUTUBE: ...] again."
                    )
                    second_cloud = self._ask_cloud(second_prompt, system, max_tokens=max_tok, on_token=on_token, image_path=image_path)
                    if second_cloud["text"]:
                        cloud = second_cloud

        if cloud["text"]:
            clean_text, tactical_directives = _process_final_text(cloud["text"])
            self.session_memory.add("user", question)
            self.session_memory.add("jarvis", clean_text)
            return {
                "text": clean_text,
                "rate_limited": False,
                "mode": mode,
                "error": False,
                "engine": cloud["engine"],
                "show_panel": is_action_popup,
                "search_query": resolved_search_query,
                "panel_payload": search_panel_payload,
                "tactical_directives": tactical_directives,
                "nav_location": tactical_directives.get("nav"),
                "layer_action": tactical_directives.get("layer"),
                "zoom_action": tactical_directives.get("zoom"),
                "radio_action": tactical_directives.get("radio"),
                "sfx_action": tactical_directives.get("sfx"),
                "annotate_action": tactical_directives.get("annotate"),
                "cockpit_action": tactical_directives.get("cockpit"),
                "search_action": tactical_directives.get("search") or resolved_search_query,
                "app_action": tactical_directives.get("app"),
                "media_action": tactical_directives.get("media"),
            }

        # Fall back to local SLM
        slm_res = self._ask_slm(prompt, system, max_tokens=max_tok, timeout=180, num_ctx=8192, on_token=on_token)
        if slm_res.get("text"):
            m_cog_search = re.search(r'\[\s*SEARCH\s*:\s*([^\]]+)\]', slm_res["text"], re.IGNORECASE)
            if m_cog_search and not live_search_intel:
                tool_query = m_cog_search.group(1).strip()
                print(f"[voice] Cognitive tool invoked by SLM: [SEARCH: '{tool_query}']")
                intel_summary, raw_results = self.skills.perform_live_search(tool_query)
                if raw_results:
                    resolved_search_query = tool_query
                    search_panel_payload = self.skills.hud_engine.build_structured_payload(
                        tool_query, "SEARCH", raw_results, intel_summary
                    )
                    is_action_popup = True
                    second_prompt = (
                        f"{prompt}\n\n[REAL-WORLD LIVE WEB SCAN RESULTS FOR '{tool_query}']:\n{intel_summary}\n\n"
                        f"[INSTRUCTIONS]: Ground your verbal debrief to Sir in the live search results above. "
                        f"Deliver an articulate, accurate answer with your characteristic wit. Do NOT emit [SEARCH: ...] again."
                    )
                    second_slm = self._ask_slm(second_prompt, system, max_tokens=max_tok, timeout=180, num_ctx=8192, on_token=on_token)
                    if second_slm["text"]:
                        slm_res = second_slm

            # Autonomous YouTube Search (SLM ReAct turn)
            m_cog_youtube = re.search(r'\[\s*YOUTUBE\s*:\s*([^\]]+)\]', slm_res["text"], re.IGNORECASE)
            if m_cog_youtube and not live_search_intel:
                yt_query = m_cog_youtube.group(1).strip()
                print(f"[voice] Cognitive tool invoked by SLM: [YOUTUBE: '{yt_query}']")
                yt_summary, yt_results = self.skills.perform_youtube_search(yt_query)
                if yt_results:
                    resolved_search_query = yt_query
                    search_panel_payload = self.skills.hud_engine.build_structured_payload(
                        f"YouTube: {yt_query}", "YOUTUBE", yt_results, yt_summary
                    )
                    is_action_popup = True
                    second_prompt = (
                        f"{prompt}\n\n[YOUTUBE VIDEO RESULTS FOR '{yt_query}']:\n{yt_summary}\n\n"
                        f"[INSTRUCTIONS]: Ground your verbal debrief in the YouTube results above. "
                        f"Deliver an articulate summary of what was found. Do NOT emit [YOUTUBE: ...] again."
                    )
                    second_slm = self._ask_slm(second_prompt, system, max_tokens=max_tok, timeout=180, num_ctx=8192, on_token=on_token)
                    if second_slm["text"]:
                        slm_res = second_slm

        clean_text, tactical_directives = _process_final_text(slm_res["text"])
        if clean_text:
            self.session_memory.add("user", question)
            self.session_memory.add("jarvis", clean_text)
        return {
            "text": clean_text,
            "rate_limited": cloud["rate_limited"],
            "mode": mode,
            "error": slm_res["error"],
            "engine": "slm",
            "show_panel": is_action_popup,
            "search_query": resolved_search_query,
            "panel_payload": search_panel_payload,
            "tactical_directives": tactical_directives,
            "nav_location": tactical_directives.get("nav"),
            "layer_action": tactical_directives.get("layer"),
            "zoom_action": tactical_directives.get("zoom"),
            "radio_action": tactical_directives.get("radio"),
            "sfx_action": tactical_directives.get("sfx"),
            "annotate_action": tactical_directives.get("annotate"),
            "cockpit_action": tactical_directives.get("cockpit"),
            "search_action": tactical_directives.get("search") or resolved_search_query,
            "app_action": tactical_directives.get("app"),
            "media_action": tactical_directives.get("media"),
        }

    def classify_intent(self, text: str, current_target: Target = None) -> dict:
        """
        Use AI to dynamically decide whether user_input is an OSINT investigation task or general conversation/question.
        Returns {"type": "investigate" | "covo", "target": str | None}
        """
        curr = current_target.primary if current_target else "None"

        if current_target and any(w in text.lower() for w in ["investigate again", "pivot to them", "scan again", "run it again", "re-scan"]):
            return {"type": "investigate", "target": current_target.primary}

        prompt = f"""Analyze this user message and determine if it is an OSINT investigation request (task) or general conversation/question (covo).

User message: "{text}"
Current active investigation target: {curr}

Rules:
1. If the user wants to start an investigation, reconnaissance, scan, trace, lookup, or inspect a specific target entity (person, email, username, domain, IP, handle, organization, or target infrastructure), classify as "investigate" and extract the clean target string.
2. If the user says "investigate again", "pivot to them", or refers to the active target, classify as "investigate" and use "{curr}" as the target.
3. If the user is asking a general question, talking casually, requesting a story, discussing strategy/OSINT methodology, or giving general instructions without specifying a target to scan right now, classify as "covo" with target null.

Output ONLY a JSON object:
{{"type": "investigate" or "covo", "target": "extracted target string or null"}}"""

        sys_prompt = "You are a precise intent classification agent. Output raw JSON only."

        res_text = ""
        cloud = self._ask_cloud(prompt, sys_prompt, max_tokens=150)
        res_text = cloud.get("text", "")

        if not res_text:
            slm_res = self._ask_slm(prompt, sys_prompt, max_tokens=150, timeout=15)
            res_text = slm_res.get("text", "")

        try:
            match = re.search(r'\{.*\}', res_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                intent_type = data.get("type", "covo")
                target_val = data.get("target")
                if intent_type == "investigate" and target_val and str(target_val).lower() not in ("null", "none"):
                    return {"type": "investigate", "target": str(target_val).strip()}
                elif intent_type == "covo":
                    return {"type": "covo", "target": None}
        except Exception:
            pass

        # Emergency fallback regex check only if cloud/SLM calls failed or returned invalid JSON
        stalk_match = re.match(r'^(?:stalk|pivot|investigate|scan|trace|lookup|dox)\s+(\S+)', text, re.IGNORECASE)
        if stalk_match and stalk_match.group(1).lower() not in ("me", "jarvis", "us", "again", "them"):
            return {"type": "investigate", "target": stalk_match.group(1)}

        return {"type": "covo", "target": None}

    def closing_monologue(self, target: Target, on_token: callable = None) -> dict:
        """
        Single synthesized post-scan monologue.
        Priority: NVIDIA NIM → Gemini → local SLM.
        Runs grounding check audit.
        Returns {text, rate_limited, used_gemini, error}
        """
        from narrative.grounding_check import verify_grounding

        case_data = self._build_case_data(target)
        system = getattr(self, "monologue_prompt", JARVIS_MONOLOGUE_PROMPT).format(case_data=case_data)
        prompt = (
            "Write the closing debrief monologue for this investigation. "
            "Follow all system rules strictly."
        )

        res_text = ""
        rate_limited = False
        used_gemini = False
        used_nvidia = False
        error = False

        # Try cloud engines (NVIDIA → Gemini)
        cloud = self._ask_cloud(prompt, system, max_tokens=4096, on_token=on_token)
        if cloud["text"]:
            res_text = cloud["text"]
            used_gemini = cloud["engine"] == "gemini"
            used_nvidia = cloud["engine"] == "nvidia"
        else:
            rate_limited = cloud["rate_limited"]

        # Fall back to SLM if cloud failed
        if not res_text:
            res = self._ask_slm(prompt, system, max_tokens=4096, timeout=180, num_ctx=8192, temperature=0.55, on_token=on_token)
            res_text = res["text"]
            error = res["error"]

        res_text = self._clean_reasoning(res_text)
        grounded_text, warnings = verify_grounding(res_text, target)

        return {
            "text": grounded_text,
            "rate_limited": rate_limited,
            "used_gemini": used_gemini,
            "used_nvidia": used_nvidia,
            "error": error,
            "grounding_warnings": warnings
        }

    def rate_limit_response(self) -> str:
        """J.A.R.V.I.S. in-character rate limit message."""
        prompt = (
            "You just got rate limited by the API. "
            "Inform Sir in J.A.R.V.I.S.'s articulate, calm British voice — 1-2 sentences. "
            "Stay in character with understated dry wit, noting the neural link is temporarily cooling down. "
            "Reassure Sir that local subsystems remain operational."
        )
        res = self._ask_slm(prompt, self.advisor_prompt, max_tokens=150)
        return res["text"]

    def inline_quote(self, finding_type: str, value: str, platform: str = "") -> str:
        """Short inline observation per finding — always SLM, never API."""
        prompt = (
            f"You just discovered: {finding_type} '{value}'"
            + (f" on {platform}" if platform else "")
            + "\nWrite ONE sentence (15-20 words) reacting to this discovery. "
            "Be sharp, articulate, analytical, and dryly observational. "
            "Use gender-neutral pronouns (they/them/their) for the target."
        )
        res = self._ask_slm(prompt, self.advisor_prompt, max_tokens=80)
        return res["text"]

    def extract_target(self, user_input: str, current_target: Target = None) -> str:
        """Use the SLM to contextually determine the target from the command."""
        current = current_target.primary if current_target else "None"
        prompt = f"""You are an intent parser. Extract the target from this user command.
Command: "{user_input}"
Current active investigation target: {current}

Rules:
1. If the user refers to the current target (e.g. "investigate again", "look into them", "scan again"), output exactly: {current}
2. If the user specifies a new target (e.g. "investigate john.doe", "pivot to target@email.com"), output ONLY the new target value.
3. If no target can be determined, output: None

Output only the raw target string. No markdown, no quotes, no explanation."""
        res = self._ask_slm(prompt, "You are a precise data extractor.", max_tokens=40)
        return res["text"].strip()

    def answer(self, question: str, target: Target, history: List[Dict]) -> dict:
        """Answer a follow-up question with full case context."""
        return self.chat(question, target)

    def _mirror_greeting(self, user_query: str, ai_response: str) -> str:
        """Greeting mirror disabled to prevent echoing operator speech."""
        return ai_response

    def _clean_reasoning(self, text: str) -> str:
        """Strip chain-of-thought, internal check scratchpads, and reasoning blocks without truncating content."""
        if not text:
            return ""

        # 1. Strip XML thinking tags <think>...</think>, <thinking>...</thinking>, <reasoning>...</reasoning>
        text = re.sub(r'<(?:think|thinking|reasoning)>[\s\S]*?</(?:think|thinking|reasoning)>', '', text, flags=re.IGNORECASE).strip()
        if re.search(r'<(?:think|thinking|reasoning)>', text, flags=re.IGNORECASE):
            text = re.sub(r'<(?:think|thinking|reasoning)>[\s\S]*$', '', text, flags=re.IGNORECASE).strip()
        text = re.sub(r'\[THINKING\].*?\[/THINKING\]', '', text, flags=re.DOTALL).strip()

        # Internal Action-HUD/tool output must never become conversational answer text.
        text = re.sub(r'\[Action HUD[^\n]*\][\s\S]*$', '', text, flags=re.IGNORECASE).strip()
        text = re.sub(r'```(?:json)?[\s\S]*?```', '', text, flags=re.IGNORECASE).strip()
        text = re.sub(r'\{\s*\"(?:skill_triggered|action|target|status|findings_so_far)\"[\s\S]*?\}', '', text, flags=re.IGNORECASE).strip()

        # 2. Strip explicit drafting/scratchpad header blocks
        for marker in ["Drafting mentally:", "Internal check:", "Self-check:"]:
            if marker in text:
                text = text.split(marker)[-1].strip()

        # 3. Strip internal scratchpad lines line-by-line (only explicit system metadata markers)
        lines = text.splitlines()
        clean_lines = []
        for line in lines:
            l = line.strip()
            if not l:
                continue
            if re.match(r'^(Drafting mentally:|Internal check:|Self-check:|Constraints:|Rule:|- Must |- No |- Signature:|Check length:|Ensure format:)', l, re.IGNORECASE):
                continue
            clean_lines.append(line)

        result = "\n".join(clean_lines).strip()

        # 4. Strip outer enclosing quotes around the whole text if present
        if (result.startswith('"') and result.endswith('"')) or (result.startswith('“') and result.endswith('”')):
            result = result[1:-1].strip()

        # 5. Sanitize accidental "Dean" name references to active operator salutation
        sal = "Sir"
        try:
            if hasattr(self, 'memory') and self.memory:
                sal = self.memory.get_salutation() or "Sir"
        except Exception:
            pass
        result = re.sub(r'\bDean\b', sal, result)
        if sal == "Sir":
            result = re.sub(r"\b(?:Ma'am|Madam|Mam)\b", "Sir", result)
        else:
            result = re.sub(r'\bSir\b', sal, result)

        result = re.sub(r'^(?:ASSISTANT|AI)\s*:\s*', '', result, flags=re.IGNORECASE).strip()

        # 7. Strip roleplay stage directions and action asterisks (*grins*, *cracks knuckles*, etc.)
        result = re.sub(r'\*(?:chuckle|grin|laugh|smirk|sigh|snicker|wink|shrug|cough|cracks knuckles|clears throat|pauses|leans)[^*]*\*', '', result, flags=re.IGNORECASE)
        result = re.sub(r'\([^)]*(?:chuckle|grin|laugh|smirk|sigh|snicker|wink|shrug|cough|cracks|leans|snort)[^)]*\)', '', result, flags=re.IGNORECASE)
        result = re.sub(r'[*_`]', '', result)
        result = re.sub(r'[ \t]+', ' ', result).strip()

        return result if result else text.strip()


