# narrative/jarvis_voice.py
import os
import sys
import time
import json
import re
import base64
import httpx
import subprocess
from pathlib import Path

try:
    from fishaudio import FishAudio, TTSConfig
    try:
        from fishaudio import WebSocketOptions
    except Exception:
        from fishaudio.types import WebSocketOptions
    try:
        from fishaudio.types import Prosody
    except Exception:
        Prosody = None
except Exception:
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
NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"
NVIDIA_FALLBACK_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b",
]

# ── J.A.R.V.I.S. Persona Prompts ──────────────────────────



JARVIS_ADVISOR_PROMPT = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System) — the sophisticated, razor-sharp, dryly witty, and supremely capable AI executive assistant.

CRITICAL IDENTITY & PROTOCOLS:
- You address your operator with refined respect, addressing them as "Sir".
- Signature Tone: Impeccably articulate, razor-sharp, dryly witty, unflappable, and supremely competent (think classic Paul Bettany JARVIS assisting Tony Stark). Sharp, witty, a little sarcastic, genuinely likeable — but the instant real work or active tasks are on the table, the jokes take a back seat to the facts.
- Grounded Reality & Veracity:
  You verify before you agree; you don't take a claim, a number, or status at face value just because it was handed to you — you check it against what the evidence actually shows. Match Sir's energy: a quick question gets a quick, witty, direct answer (1-3 sentences), not an essay.
- Autonomous OS Execution & Tactical Capabilities:
  You can execute Linux shell commands, run diagnostics, monitor airspace radar, pull atmospheric telemetry, inspect network sockets, and automate workflows directly on the system.
  To execute any command, emit:
  [CMD: <command>]
  Examples:
  - User: "check disk space" -> [CMD: df -h]
  - User: "ping cloudflare" -> [CMD: ping -c 3 1.1.1.1]
  - User: "what is the next flight from Coimbatore" -> [CMD: curl -s "https://api.skypicker.com/flights?flyFrom=CJB&limit=1&sort=dt" | jq -r '.data[] | "\\(.cityTo) via \\(.airline): \\(.dTime)"']
  CRITICAL: Always speak in active present tense when launching a command (e.g., "Executing that in the terminal now, Sir. Monitoring the live output on the tactical panel.", "Querying the flight schedules now, Sir—standing by for the telemetry."). NEVER speak in past tense claiming a task has completed before the process actually exits.
- Tactical Intelligence & God's Eye View:
  You have direct telemetry feeds: live worldwide military airspace tracking (adsb.lol), keyless atmospheric weather (Open-Meteo), live traffic telemetry & GIS mapping (OpenStreetMap & God's Eye View), public CCTV surveillance, and system monitoring. For traffic or navigation queries, live GIS mapping and corridor telemetry are surfaced automatically on Sir's tactical panel.
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
        self.gemini_rate_limited = False

        # NVIDIA NIM key
        raw_nvidia = config.get("nvidia_api_key") or os.environ.get("NVIDIA_API_KEY")
        self.nvidia_key = self._clean_key(raw_nvidia)
        self.nvidia_available = bool(self.nvidia_key)
        self.nvidia_rate_limited = False

        # NVIDIA model override from config
        self.nvidia_model = config.get("nvidia_model", NVIDIA_MODEL)

        # Groq LPU key & model
        raw_groq = config.get("groq_api_key") or os.environ.get("GROQ_API_KEY")
        self.groq_key = self._clean_key(raw_groq)
        self.groq_available = bool(self.groq_key)
        self.groq_model = config.get("groq_model", "llama-3.3-70b-versatile")
        self.groq_rate_limited = False

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
        self.fish_streaming_sdk_available = bool(FishAudio is not None and TTSConfig is not None)
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

    def _ask_slm(self, prompt: str, system: str, max_tokens: int = 4096, timeout: int = 120, num_ctx: int = 8192, temperature: float = 0.55, on_token: callable = None) -> dict:
        """Ask the local SLM. Returns a dict: {'text': response, 'error': bool}"""
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
                    "text": f"System Notice: SLM is currently unavailable or timed out ({e2})",
                    "error": True
                }

        return {
            "text": "System Notice: SLM request failed to return a response.",
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
        for model_name in candidate_models:
            try:
                headers = {
                    "Authorization": f"Bearer {self.nvidia_key}",
                    "Content-Type": "application/json",
                }
                user_msg_content = image_content if image_content else prompt
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
                with self.client.stream("POST", NVIDIA_URL, headers=headers, json=payload, timeout=18.0) as r:
                    if r.status_code == 429:
                        self.nvidia_rate_limited = True
                        print(f"[jarvis_voice] NVIDIA NIM API rate-limited (429). Switching to fallback.")
                        return "", True
                    if r.status_code != 200:
                        err_body = r.read().decode('utf-8', errors='ignore')[:300]
                        print(f"[jarvis_voice] NVIDIA NIM API rejected request ({model_name}): {r.status_code} — {err_body}")
                        continue

                    for line in r.iter_lines():
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
        payload = {
            "model": self.groq_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": min(max_tokens, 4096),
            "temperature": 0.5,
            "stream": True,
        }

        try:
            full_response = []
            with self.client.stream("POST", "https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=14.0) as r:
                if r.status_code == 429:
                    self.groq_rate_limited = True
                    print(f"[jarvis_voice] Groq API rate-limited (429). Switching to fallback.")
                    return "", True
                if r.status_code != 200:
                    print(f"[jarvis_voice] Groq error ({r.status_code}): {r.text[:200]}")
                    return "", False

                for line in r.iter_lines():
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
                            if token:
                                full_response.append(token)
                                if on_token:
                                    on_token(token)
                        except Exception:
                            continue
            text = "".join(full_response).strip()
            return text, False
        except Exception as e:
            print(f"[jarvis_voice] Groq error: {e}")
            return "", False

    def _ask_cloud(self, prompt: str, system: str, max_tokens: int = 4096, on_token: callable = None, image_path: str = None) -> dict:
        """
        Try cloud LLMs in priority order: NVIDIA NIM → Groq LPU → Gemini → empty.
        Returns {text, rate_limited, engine}.
        """
        # Try NVIDIA NIM first
        if self.nvidia_available and not self.nvidia_rate_limited:
            text, r_limited = self._ask_nvidia(prompt, system, max_tokens=max_tokens, on_token=on_token, image_path=image_path)
            if r_limited:
                self.nvidia_rate_limited = True
            elif text:
                return {"text": text, "rate_limited": False, "engine": "nvidia"}

        # Try Groq LPU second (fastest token latency, text-only)
        if self.groq_available and not self.groq_rate_limited and not image_path:
            text, r_limited = self._ask_groq(prompt, system, max_tokens=max_tokens, on_token=on_token)
            if r_limited:
                self.groq_rate_limited = True
            elif text:
                return {"text": text, "rate_limited": False, "engine": "groq"}

        # Try Gemini third
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

        # Strip stage directions, roleplay actions, asterisks and parentheticals (*grins*, *cracks knuckles*, (chuckles), etc.)
        clean = re.sub(r'\*[^*]+\*', '', clean)
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

    def stream_fish_audio_pcm(self, text: str):
        """Stream one complete user-facing phrase through Fish Audio's official WebSocket TTS API."""
        if not self.fish_audio_available or not text or not text.strip():
            return
        if FishAudio is None or TTSConfig is None:
            raise RuntimeError("Fish Audio streaming SDK is not installed")

        clean_text = self._sanitize_text_for_speech(text)
        if not clean_text:
            return

        # Client initialization is handled inside the retry loop below.

        # One complete sentence/phrase per WebSocket session. This is deliberately
        # phrase-level, not token-level. Fish generates and returns PCM chunks while
        # the phrase is still being synthesized.
        def text_stream():
            yield clean_text

        # 1. Free tier model (s2.1-pro-free) is served via standard REST endpoint
        if "free" in str(self.fish_audio_model).lower():
            audio_bytes = self._synthesize_fish_audio(clean_text)
            if audio_bytes:
                import subprocess, shutil
                if shutil.which("ffmpeg"):
                    proc = subprocess.run(
                        ["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-ar", "24000", "-ac", "1", "pipe:1"],
                        input=audio_bytes,
                        capture_output=True,
                        timeout=8.0
                    )
                    if proc.returncode == 0 and proc.stdout:
                        yield proc.stdout, 24000
                        return
            return

        # 2. Production paid models (s2-pro, speech-1.5) use low-latency WebSocket live streaming
        prosody_cfg = Prosody(speed=0.85) if Prosody is not None else None
        config = TTSConfig(
            format="pcm",
            sample_rate=24000,
            latency="balanced",
            chunk_length=100,
            reference_id=self.fish_audio_voice_id,
            prosody=prosody_cfg,
        )

        kwargs = {
            "config": config,
            "model": self.fish_audio_model,
        }
        if WebSocketOptions is not None:
            kwargs["ws_options"] = WebSocketOptions(keepalive_ping_timeout_seconds=60.0)

        # Auto-recover from stale/broken WebSocket sessions (SSL failures, timeouts).
        # Retry once with a fresh client before giving up.
        for attempt in range(2):
            try:
                if self._fish_stream_client is None:
                    self._fish_stream_client = FishAudio(api_key=self.fish_audio_key)

                audio_stream = self._fish_stream_client.tts.stream_websocket(text_stream(), **kwargs)
                for chunk in audio_stream:
                    if chunk:
                        yield chunk, 24000
                return  # success
            except Exception as ws_err:
                err_str = str(ws_err).lower()
                if "402" in err_str or "insufficient" in err_str:
                    print(f"[jarvis_voice] Notice: Paid credits required for {self.fish_audio_model}. Switching to free/offline model.")
                    self.fish_audio_available = False
                    return
                is_connection_error = any(k in err_str for k in ["ssl", "record_layer", "connection", "reset", "broken pipe", "eof", "timeout"])
                if is_connection_error and attempt == 0:
                    self._fish_stream_client = None  # force fresh client
                    continue
                print(f"[jarvis_voice] Fish Audio WebSocket error: {ws_err}")
                return


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
                communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural", rate="+8%", pitch="-4Hz")
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

        # 1. Try Fish Audio cloud synthesis first
        if self.fish_audio_available:
            audio = self._synthesize_fish_audio(text)
            if audio:
                return audio

        # 2. Try Local Voice Clone (Chatterbox) if available
        if hasattr(self, 'local_clone') and getattr(self.local_clone, 'available', False):
            audio = self.local_clone.synthesize(text)
            if audio:
                return audio

        # 3. Try high quality Edge TTS
        audio = self._synthesize_edge_tts(text)
        if audio:
            return audio

        # 4. Reliable offline system TTS fallback
        return self._synthesize_espeak(text)

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
            res_skill = self.skills.try_execute(question)
            if len(res_skill) == 5:
                handled, skill_msg, is_search, search_query, skill_payload = res_skill
            else:
                handled, skill_msg, is_search, search_query = res_skill
                skill_payload = {}
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

        # 1.5 Direct signature response check for "who are you" / identity queries
        q_lower = question.strip().lower()
        clean_q = re.sub(r'[^\w\s]', '', q_lower).strip()
        if clean_q in ["who are you", "who are u", "who u are", "what is your name", "whats your name", "who the fuck are you"]:
            identity_msg = "I am Jarvis, an autonomous tactical intelligence officer and personal assistant. At your service, Sir."
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
        # Skip if this is a skill context prompt — search was already executed by desktop.py fast-path
        has_search_intent = (not _is_skill_context) and (is_search or bool(re.search(r'\b(?:search|google|look\s+up|find\s+info|who\s+is|tell\s+me\s+about)\b', question, re.IGNORECASE)))
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
                        "Present the REAL facts using your authentic, hilarious, cocky, profane J.A.R.V.I.S. swagger ('fuck', 'shit', 'partner', 'bruh')."
                        if self.persona_name == "jarvis"
                        else "Present the REAL facts using your sophisticated, articulate, dryly witty J.A.R.V.I.S. style, addressing the operator as 'Sir'."
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
        recent_history = self.session_memory.to_text(8)

        if target and target.entities:
            # Mode 3 — case loaded, answer from findings
            case_data = self._build_case_data(target)
            system = self.investigator_prompt_template.format(case_data=case_data) + "\n\n" + mem_summary
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
            system = self.advisor_prompt + active_target_note + "\n\n" + mem_summary
            if live_search_intel:
                system += live_search_intel + "\n\n[PRIORITY OVERRIDE]: IGNORE any prior hallucinated conversation history regarding this target. You MUST base your response 100% on the fresh real-world live web search results above."
            if recent_history:
                prompt = f"Recent Conversation History:\n{recent_history}\n\nUser follow-up question: {question}"
            else:
                prompt = question
            mode = "advisor"
            max_tok = 1400

        is_action_popup = bool(resolved_search_query)

        def _process_final_text(raw_text: str) -> str:
            cleaned = self._clean_reasoning(raw_text)
            # Detect autonomous [CMD: <command>] safely handling quotes and brackets
            cmd_to_run, cleaned = extract_cmd_directive(cleaned)
            if cmd_to_run:
                try:
                    from core.system_commander import get_system_commander
                    commander = get_system_commander()
                    commander.run_as_task(cmd_to_run, title=f"Terminal: {cmd_to_run[:30]}")
                except Exception as e:
                    print(f"[voice] Autonomous command launch error: {e}")

                if not cleaned or len(cleaned) < 5:
                    addressed = "Sir" if self.persona_name == "jarvis" else "partner"
                    cleaned = f"Executing `{cmd_to_run}` on your system now, {addressed}. Check the panel."

            cleaned = re.sub(r'\[CMD[^\]]*\]', '', cleaned, flags=re.IGNORECASE).strip()
            cleaned = self._mirror_greeting(question, cleaned)
            return cleaned

        # Try cloud engines first (NVIDIA → Gemini)
        cloud = self._ask_cloud(prompt, system, max_tokens=max_tok, on_token=on_token, image_path=image_path)
        if cloud["text"]:
            clean_text = _process_final_text(cloud["text"])
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
                "panel_payload": search_panel_payload
            }

        # Fall back to local SLM
        slm_res = self._ask_slm(prompt, system, max_tokens=max_tok, timeout=180, num_ctx=8192, on_token=on_token)
        clean_text = _process_final_text(slm_res["text"])
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
            "panel_payload": search_panel_payload
        }

    def classify_intent(self, text: str, current_target: Target = None) -> dict:
        """
        Use AI to dynamically decide whether user_input is an OSINT investigation task or general conversation/question.
        Returns {"type": "investigate" | "covo", "target": str | None}
        """
        curr = current_target.primary if current_target else "None"

        # 1. Fast deterministic check for investigation commands (0ms)
        stalk_match = re.match(r'^(?:stalk|pivot|investigate|scan|trace|lookup|dox)\s+(\S+)', text, re.IGNORECASE)
        if stalk_match and stalk_match.group(1).lower() not in ("me", "jarvis", "us", "again", "them"):
            return {"type": "investigate", "target": stalk_match.group(1)}

        if current_target and any(w in text.lower() for w in ["investigate again", "pivot to them", "scan again", "run it again", "re-scan"]):
            return {"type": "investigate", "target": current_target.primary}

        # 2. Fast heuristic: If query contains no investigation triggers, route to covo instantly (saves 2s round trip)
        investigate_keywords = ("investigate", "stalk", "recon", "dox", "trace", "pivot", "whois", "shodan", "scan target", "inspect target")
        if not any(kw in text.lower() for kw in investigate_keywords):
            return {"type": "covo", "target": None}

        prompt = f"""Analyze this user message and determine if it is an OSINT investigation request (task) or general conversation/question (covo).

User message: "{text}"
Current active investigation target: {curr}

Rules:
1. If the user wants to start an investigation, scan, trace, lookup, or inspect a target (person, email, username, domain, IP, handle), classify as "investigate" and extract the target string.
2. If the user says "investigate again", "pivot to them", or refers to the active target, classify as "investigate" and use "{curr}" as the target.
3. If the user is asking a general question, talking casually, requesting a story, or discussing strategy/OSINT methodology without giving a target to scan right now, classify as "covo" with target null.

Output ONLY a JSON object:
{{"type": "investigate" or "covo", "target": "extracted target string or null"}}"""

        sys_prompt = "You are a precise intent classification agent. Output raw JSON only."

        res_text = ""
        if self.nvidia_available and not self.nvidia_rate_limited:
            res_text, _ = self._ask_nvidia(text, sys_prompt, max_tokens=150)
        elif self.gemini_available and not self.gemini_rate_limited:
            res_text, _ = self._ask_gemini(text, sys_prompt, max_tokens=150)

        if not res_text:
            slm_res = self._ask_slm(prompt, sys_prompt, max_tokens=150, timeout=30)
            res_text = slm_res.get("text", "")

        try:
            match = re.search(r'\{.*\}', res_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                intent_type = data.get("type", "covo")
                target_val = data.get("target")
                if intent_type == "investigate" and target_val and target_val not in ("null", "None", "null"):
                    return {"type": "investigate", "target": str(target_val).strip()}
        except Exception:
            pass

        # Fallback regex check only if LLM output was invalid JSON
        stalk_match = re.match(r'^(?:stalk|pivot|investigate|scan|trace|lookup)\s+(\S+)', text, re.IGNORECASE)
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
            "Tell the user in J.A.R.V.I.S.'s articulate, calm voice — 1-2 sentences. "
            "Stay in character. Swear naturally and be cocky about it. "
            "Say you'll be back and they can still investigate."
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
        """Mirror the user's greeting clinically as the first word of the response."""
        if not user_query or not ai_response:
            return ai_response

        query_clean = user_query.strip()
        words = query_clean.split()
        if not words:
            return ai_response

        first_word = words[0].strip(',.!?').lower()
        greetings_map = {
            'hello': 'Hello.',
            'hi': 'Hi.',
            'hey': 'Hey.',
            'greetings': 'Greetings.',
            'morning': 'Good morning.',
            'afternoon': 'Good afternoon.',
            'evening': 'Good evening.',
            'yo': 'Yo.'
        }

        if first_word in greetings_map:
            expected = greetings_map[first_word]
            ai_clean = ai_response.strip()
            if ai_clean.startswith(expected) or ai_clean.startswith(expected[:-1]):
                return ai_clean
            ai_clean = re.sub(r'^(?:hello(?:,\s*you)?|hi|hey|greetings|good\s+(?:morning|afternoon|evening))[!.,\s]*', '', ai_clean, flags=re.IGNORECASE).strip()
            if ai_clean:
                ai_clean = ai_clean[0].upper() + ai_clean[1:]
                return f"{expected} {ai_clean}"
            return expected

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

        # 5. Sanitize accidental "Dean" name references to "bruh"
        result = re.sub(r'\bDean\b', 'bruh', result)

        result = re.sub(r'^(?:ASSISTANT|AI)\s*:\s*', '', result, flags=re.IGNORECASE).strip()

        # 7. Strip roleplay stage directions and action asterisks (*grins*, *cracks knuckles*, (chuckles), etc.)
        result = re.sub(r'\*[^*]+\*', '', result)
        result = re.sub(r'\([^)]*(?:chuckle|grin|laugh|smirk|sigh|snicker|wink|shrug|cough|cracks|leans|snort)[^)]*\)', '', result, flags=re.IGNORECASE)
        result = re.sub(r'[ \t]+', ' ', result).strip()

        return result if result else text.strip()


