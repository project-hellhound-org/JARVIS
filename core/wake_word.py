# core/wake_word.py
"""
Local openWakeWord engine & VAD command-window management for J.A.R.V.I.S.
Features:
- Continuous on-device CPU prediction for "Hey JARVIS" / "JARVIS".
- Hardware mic detection with graceful fallback to browser Web Speech API.
- Voice Activity Detection (VAD) for post-speech silence handling.
"""

import os
import sys
import time
import math
import struct
import threading
import queue
import json
import re
from pathlib import Path
from typing import Callable, Optional, Dict, Any

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from contextlib import contextmanager

_wake_word_notice_logged = False

@contextmanager
def no_c_stderr():
    """Suppress C-level stderr output (e.g. libjack / libasound error spam)."""
    sys.stderr.flush()
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)
        os.close(devnull)
        try:
            yield
        finally:
            sys.stderr.flush()
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
    except Exception:
        yield

def _patch_openwakeword_providers():
    """Align openwakeword ONNX providers with actual available system providers to avoid CUDA fallback warnings."""
    try:
        import onnxruntime as ort
        import openwakeword.utils
        avail_providers = ort.get_available_providers()

        orig_init = openwakeword.utils.AudioFeatures.__init__
        def safe_audio_features_init(self, melspec_onnx_model_path=None, embedding_onnx_model_path=None, sr=16000, ncpu=1):
            if melspec_onnx_model_path is None:
                melspec_onnx_model_path = openwakeword.utils.os.path.join(
                    openwakeword.utils.pathlib.Path(openwakeword.utils.__file__).parent.resolve(),
                    "resources", "models", "melspectrogram.onnx"
                )
            if embedding_onnx_model_path is None:
                embedding_onnx_model_path = openwakeword.utils.os.path.join(
                    openwakeword.utils.pathlib.Path(openwakeword.utils.__file__).parent.resolve(),
                    "resources", "models", "embedding_model.onnx"
                )

            sessionOptions = ort.SessionOptions()
            sessionOptions.inter_op_num_threads = ncpu
            sessionOptions.intra_op_num_threads = ncpu
            self.melspec_model = ort.InferenceSession(melspec_onnx_model_path, sess_options=sessionOptions, providers=avail_providers)
            self.embedding_model = ort.InferenceSession(embedding_onnx_model_path, sess_options=sessionOptions, providers=avail_providers)
            self.onnx_execution_provider = self.melspec_model.get_providers()[0]

            self.raw_data_buffer = openwakeword.utils.deque(maxlen=sr*10)
            self.melspectrogram_buffer = openwakeword.utils.np.ones((76, 32))
            self.melspectrogram_max_len = 10*97
            self.accumulated_samples = 0
            self.feature_buffer = self._get_embeddings(openwakeword.utils.np.zeros(160000).astype(openwakeword.utils.np.int16))
            self.feature_buffer_max_len = 120

        openwakeword.utils.AudioFeatures.__init__ = safe_audio_features_init
    except Exception:
        pass


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between s1 and s2 in pure Python (0ms overhead)."""
    if s1 == s2:
        return 0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return max(len1, len2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]
    for i in range(len1 + 1):
        dp[i][0] = i
    for j in range(len2 + 1):
        dp[0][j] = j
    for i in range(1, len1 + 1):
        c1 = s1[i - 1]
        for j in range(1, len2 + 1):
            cost = 0 if c1 == s2[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[len1][len2]


def _levenshtein_ratio(s1: str, s2: str) -> float:
    """Compute string similarity ratio (0.0 to 1.0) using Levenshtein distance in pure Python (0ms overhead)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    dist = _levenshtein_distance(s1, s2)
    max_len = max(len1, len2)
    return 1.0 - (dist / max_len)



def _phonetic_code(word: str) -> str:
    """Fast local Soundex-like phonetic encoding in pure Python (100% on-device, zero network)."""
    w = re.sub(r'[^a-zA-Z]', '', word.upper())
    if not w:
        return ""
    first_letter = w[0]
    codes = {
        'B': '1', 'F': '1', 'P': '1', 'V': '1',
        'C': '2', 'G': '2', 'J': '2', 'K': '2', 'Q': '2', 'S': '2', 'X': '2', 'Z': '2',
        'D': '3', 'T': '3',
        'L': '4',
        'M': '5', 'N': '5',
        'R': '6'
    }
    encoded = [first_letter]
    prev = codes.get(first_letter, '0')
    for char in w[1:]:
        curr = codes.get(char, '0')
        if curr != '0' and curr != prev:
            encoded.append(curr)
        prev = curr
    return "".join(encoded[:4]).ljust(4, '0')


class WakeWordEngine:
    def __init__(
        self,
        on_wake_detected: Optional[Callable[[str], None]] = None,
        on_speech_ended: Optional[Callable[[], None]] = None,
        on_follow_up_expired: Optional[Callable[[], None]] = None,
        audio_chunk_callback: Optional[Callable[[bytes], None]] = None,
        model_path: Optional[str] = None,
        wake_phrases: Optional[list] = None,
        threshold: float = 0.60,
        silence_timeout_sec: float = 0.8,
        max_window_sec: float = 12.0
    ):
        self.on_wake_detected = on_wake_detected
        self.on_speech_ended = on_speech_ended
        self.on_follow_up_expired = on_follow_up_expired
        self.audio_chunk_callback = audio_chunk_callback

        # Configurable multi-wake phrase list (sorted longest-first so multi-word phrases take precedence)
        default_phrases = [
            "jarvis", "hey jarvis", "jarvis you there", "wake up jarvis", "alright jarvis", "yo jarvis", "ok jarvis"
        ]
        raw_list = [p.strip().lower() for p in (wake_phrases or default_phrases) if p.strip()]
        self.wake_phrases = sorted(raw_list, key=lambda p: (len(p.split()), len(p)), reverse=True)

        # Load dynamic VAD settings
        vad_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "vad_settings.json")
        loaded_silence = silence_timeout_sec
        if os.path.exists(vad_file):
            try:
                with open(vad_file, "r") as f:
                    vdata = json.load(f)
                    loaded_silence = max(0.6, float(vdata.get("speech_hangover_ms", 800)) / 1000.0)
            except Exception:
                pass

        self.threshold = threshold
        self.silence_timeout_sec = loaded_silence
        self.max_window_sec = max_window_sec

        self.is_muted = False
        self.hardware_mic_available = False
        self.fallback_to_web_speech = False
        self.running = False
        self.thread: Optional[threading.Thread] = None

        self._model = None
        self._audio_stream = None
        self._pyaudio = None

        # VAD & Window State
        self.is_window_active = False
        self.window_start_time = 0.0
        self.last_speech_time = 0.0
        self.has_detected_speech_in_window = False
        self._wake_cooldown_until = 0.0

        # Continued-Conversation (Open-Mic Follow-up Window) State
        self.is_follow_up_mode = False
        self.follow_up_expires = 0.0

        self._init_engine(model_path)

    def start_follow_up_window(self, duration_sec: float = 10.0):
        """
        Activate Continued-Conversation mode (open mic without requiring wake phrase).
        VAD actively listens and buffers speech until speech finishes or duration elapses in silence.
        """
        if getattr(self, 'is_muted', False):
            return
        now = time.time()
        self.is_follow_up_mode = True
        self.follow_up_expires = now + max(4.0, duration_sec)
        self.is_window_active = True
        self.window_start_time = now
        self.last_speech_time = now
        self.has_detected_speech_in_window = False
        self._wake_cooldown_until = 0.0
        print(f"[wake_word] Continued-conversation window ACTIVE for {duration_sec:.1f}s (open mic, no wake word needed).")

    def cancel_follow_up_window(self):
        """Immediately cancel follow-up listening mode."""
        self.is_follow_up_mode = False
        self.follow_up_expires = 0.0
        self.is_window_active = False

    def is_in_follow_up(self) -> bool:
        """Returns True if open-mic follow-up window is active and unexpired."""
        return not getattr(self, 'is_muted', False) and self.is_follow_up_mode and time.time() < self.follow_up_expires

    def set_muted(self, muted: bool):
        """Enable or disable wake-word listening and hardware audio processing."""
        self.is_muted = bool(muted)
        if self.is_muted:
            self.is_window_active = False
            self.is_follow_up_mode = False
            self.follow_up_expires = 0.0
            self._wake_cooldown_until = 0.0
            if self._model:
                try:
                    self._model.reset()
                except Exception:
                    pass
            if self._audio_stream and hasattr(self._audio_stream, 'is_active'):
                try:
                    if self._audio_stream.is_active():
                        self._audio_stream.stop_stream()
                except Exception:
                    pass
            print("[wake_word] Wake engine MUTED: hardware stream stopped, zero CPU.")
        else:
            if self._audio_stream and hasattr(self._audio_stream, 'is_stopped'):
                try:
                    if self._audio_stream.is_stopped():
                        self._audio_stream.start_stream()
                except Exception:
                    pass
            print("[wake_word] Wake engine UNMUTED: hardware stream active.")

    def reset_cooldown(self, seconds: float = 2.0):
        """End active capture and suppress wake detection for the cooldown period."""
        self.is_window_active = False
        self.is_follow_up_mode = False
        self._wake_cooldown_until = time.time() + seconds
        if self._model:
            try:
                self._model.reset()
            except Exception:
                pass

    def _init_engine(self, model_path: Optional[str]):
        """Initialize openWakeWord model and check microphone hardware."""
        _patch_openwakeword_providers()
        target_model = model_path
        if not target_model:
            custom_jarvis_path = Path(__file__).parent.parent / "data" / "models" / "hey_jarvis.onnx"
            if custom_jarvis_path.exists():
                target_model = str(custom_jarvis_path)

        missing_optional = False
        try:
            import openwakeword
            from openwakeword.model import Model
            if not target_model:
                model_dir = Path(openwakeword.__file__).parent / "resources" / "models"
                candidates = [
                    model_dir / "hey_jarvis_v0.1.onnx",
                    model_dir / "hey_jarvis.onnx",
                ]
                for c in candidates:
                    if c.exists():
                        target_model = str(c)
                        break

            if target_model and os.path.exists(target_model):
                print(f"[wake_word] Loading openWakeWord model: {target_model}")
                self._model = Model(wakeword_model_paths=[target_model])
                print("[wake_word] Native openWakeWord engine active for JARVIS.")
            else:
                self._model = None
        except (ImportError, ModuleNotFoundError):
            missing_optional = True
            self._model = None
        except Exception as e:
            print(f"[wake_word] Notice: openWakeWord model init: {e}. Active via Web Speech STT.")
            self._model = None

        # 2. Check microphone hardware (suppress C-level JACK/ALSA stderr spam)
        try:
            import pyaudio
            with no_c_stderr():
                self._pyaudio = pyaudio.PyAudio()
                device_count = self._pyaudio.get_device_count()
            has_input = False
            for i in range(device_count):
                with no_c_stderr():
                    info = self._pyaudio.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    has_input = True
                    break
            
            if has_input:
                self.hardware_mic_available = True
                print("[wake_word] Hardware microphone detected and ready for JARVIS.")
            else:
                print("[wake_word] No audio input hardware found. Delegating wake word to Web Speech API.")
                self.fallback_to_web_speech = True
        except (ImportError, ModuleNotFoundError):
            missing_optional = True
            self.fallback_to_web_speech = True
        except Exception as e:
            print(f"[wake_word] Audio hardware init notice: {e}. Falling back to Web Speech API.")
            self.fallback_to_web_speech = True

        if missing_optional:
            global _wake_word_notice_logged
            if not _wake_word_notice_logged:
                print("[wake_word] Wake word disabled: install requirements-optional.txt to enable")
                _wake_word_notice_logged = True

    def start(self):
        """Start background microphone listening thread if hardware is present."""
        if not self.hardware_mic_available or not self._model:
            if not _wake_word_notice_logged:
                print("[wake_word] Engine running in browser Web Speech API fallback mode for JARVIS.")
            return

        self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop microphone listening loop."""
        self.running = False
        if self._audio_stream:
            try:
                self._audio_stream.stop_stream()
                self._audio_stream.close()
            except Exception:
                pass
        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass

    def _compute_rms(self, pcm_data: bytes) -> float:
        """Compute Root Mean Square (RMS) volume level of 16-bit PCM chunk."""
        if not pcm_data:
            return 0.0
        count = len(pcm_data) // 2
        if count == 0:
            return 0.0
        shorts = struct.unpack(f"<{count}h", pcm_data)
        sum_squares = sum(s * s for s in shorts)
        return math.sqrt(sum_squares / count)

    def check_stt_text_for_wake_or_aliases(self, text: str) -> tuple[bool, str]:
        """
        Check incoming raw STT transcription against registered JARVIS wake phrases.
        Strict matcher (Part 3):
        - Accept explicit list of spellings: jarvis, j.a.r.v.i.s, jarc, j.a.r.c, jarvik, jarbis, jervis, javis
        - Plus edit distance 1 ONLY for words of 5+ letters
        - Reject common words and names: jar, jars, gary, jared, jarrod, jarrett, yard, yarn, garvis, car, park, part...
        - Wake word counts only at the start of an utterance or right after hey/ok/yo/wake up (or alright/hi/hello).
        Returns (matched: bool, phrase: str)
        """
        if not text:
            return False, ""
        clean = text.strip().lower()
        # Normalize spelled-out or dotted acronyms (e.g. "j.a.r.c.", "j. a. r. c.", "j a r c" -> "jarc"; "j.a.r.v.i.s." -> "jarvis")
        clean_norm = re.sub(r'\bj\s*\.?\s*a\s*\.?\s*r\s*\.?\s*c\.?\b', 'jarc', clean, flags=re.IGNORECASE)
        clean_norm = re.sub(r'\bj\s*\.?\s*a\s*\.?\s*r\s*\.?\s*v\s*\.?\s*i\s*\.?\s*s\.?\b', 'jarvis', clean_norm, flags=re.IGNORECASE)
        clean_no_punct = re.sub(r'[^\w\s]', '', clean_norm)
        words = clean_no_punct.split()
        if not words:
            return False, ""

        EXPLICIT_WAKE_SPELLINGS = {
            "jarvis", "jarc", "jarvik", "jarbis", "jervis", "javis",
            "jarvis", "jarc"
        }
        EDIT_DISTANCE_BASE_WORDS = {
            "jarvis", "jarvik", "jarbis", "jervis", "javis"
        }
        REJECTED_WORDS = {
            "jar", "jars", "gary", "jared", "jarrod", "jarrett", "yard", "yarn", "garvis",
            "car", "cars", "park", "parks", "part", "parts", "dark", "dart", "darts",
            "card", "cards", "hard", "hart", "bar", "bars", "tar", "tars", "far", "farm",
            "farms", "mar", "mars", "star", "stars", "war", "wars", "par", "jarred",
            "jordan", "jason", "jacob", "james", "jerry", "larry", "barry", "harry",
            "garret", "garrett"
        }

        def _is_wake_word(w: str) -> bool:
            if not w:
                return False
            w_lower = w.lower().strip()
            if w_lower in REJECTED_WORDS:
                return False
            if w_lower in EXPLICIT_WAKE_SPELLINGS:
                return True
            # Edit distance 1 ONLY for words with 5+ letters
            if len(w_lower) >= 5:
                for base in EDIT_DISTANCE_BASE_WORDS:
                    if _levenshtein_distance(w_lower, base) <= 1:
                        return True
            return False

        # Wake word counts only at utterance start or right after hey/ok/yo/wake up (or alright/hi/hello)
        prefixes = [
            (["hey", "wake", "up"], "Wake Up Jarvis"),
            (["wake", "up"], "Wake Up Jarvis"),
            (["hey"], "Hey Jarvis"),
            (["ok"], "OK Jarvis"),
            (["okay"], "OK Jarvis"),
            (["yo"], "Yo Jarvis"),
            (["alright"], "Alright Jarvis"),
            (["hi"], "Hi Jarvis"),
            (["hello"], "Hello Jarvis"),
        ]

        # 1. Check prefixed positions
        for pfx_tokens, pfx_label in prefixes:
            n_pfx = len(pfx_tokens)
            if len(words) > n_pfx and words[:n_pfx] == pfx_tokens:
                cand = words[n_pfx]
                if _is_wake_word(cand):
                    return True, pfx_label

        # 2. Check utterance start
        if words and _is_wake_word(words[0]):
            # Check multi-word phrase match in wake_phrases
            for phrase in self.wake_phrases:
                phrase_clean = re.sub(r'[^\w\s]', '', phrase).lower()
                if clean_no_punct.startswith(phrase_clean):
                    return True, phrase.title()
            return True, "Jarvis"

        return False, ""

    def trigger_wake_event(self, trigger_phrase: str = "Hey JARVIS"):
        """Programmatically trigger a wake detection event (e.g. from STT regex fallback)."""
        if getattr(self, 'is_muted', False):
            return
        now = time.time()
        self.is_window_active = True
        self.window_start_time = now
        self.last_speech_time = now
        self.has_detected_speech_in_window = False
        self._wake_cooldown_until = now + 4.0
        if self._model:
            try:
                self._model.reset()
            except Exception:
                pass
        if self.on_wake_detected:
            self.on_wake_detected(trigger_phrase)

    def _listen_loop(self):
        """Background continuous audio streaming loop."""
        CHUNK_SIZE = 1280  # 80ms chunk at 16kHz
        FORMAT = 8  # pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000

        try:
            import pyaudio
            with no_c_stderr():
                self._audio_stream = self._pyaudio.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK_SIZE
                )
        except Exception as e:
            print(f"[wake_word] Failed to open microphone stream: {e}. Switching to browser STT.")
            self.hardware_mic_available = False
            self.fallback_to_web_speech = True
            return

        import numpy as np
        print("[wake_word] Continuous openWakeWord background listener active (JARVIS).")

        while self.running:
            if getattr(self, 'is_muted', False):
                time.sleep(0.08)
                continue
            try:
                data = self._audio_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                if not data:
                    continue

                if self.audio_chunk_callback:
                    try:
                        self.audio_chunk_callback(data)
                    except Exception:
                        pass

                audio_int16 = np.frombuffer(data, dtype=np.int16)
                now = time.time()

                # 1. Run openWakeWord prediction ONLY when command capture window is NOT active and cooldown elapsed
                if self._model and not self.is_window_active and now >= self._wake_cooldown_until:
                    prediction = self._model.predict(audio_int16)
                    for model_name, score in prediction.items():
                        if score >= self.threshold:
                            print(f"[wake_word] JARVIS detected! Score: {score:.3f} >= {self.threshold:.3f}")
                            self.trigger_wake_event("Hey JARVIS")
                            break
                        elif score >= 0.20:
                            print(f"[wake_word] Candidate voice detected: {score:.3f} (threshold: {self.threshold:.3f})")

                # 2. Run Voice Activity Detection (VAD) for active command or follow-up window
                if self.is_window_active:
                    elapsed = now - self.window_start_time
                    rms = self._compute_rms(data)

                    # Voice Activity Detected
                    if rms > 180.0:
                        self.last_speech_time = now
                        self.has_detected_speech_in_window = True
                        if self.is_follow_up_mode:
                            # Keep extending open-mic follow-up window while operator is speaking
                            self.follow_up_expires = max(self.follow_up_expires, now + 4.0)

                    silence_duration = now - self.last_speech_time

                    # Case A: User finished speaking (silence after speech)
                    if self.has_detected_speech_in_window and silence_duration >= self.silence_timeout_sec:
                        self.is_window_active = False
                        self.is_follow_up_mode = False
                        self._wake_cooldown_until = time.time() + 1.5
                        if self._model:
                            try:
                                self._model.reset()
                            except Exception:
                                pass
                        if self.on_speech_ended:
                            self.on_speech_ended()

                    # Case B: Maximum capture window safety cap reached
                    elif elapsed >= self.max_window_sec:
                        self.is_window_active = False
                        self.is_follow_up_mode = False
                        self._wake_cooldown_until = time.time() + 2.0
                        if self._model:
                            try:
                                self._model.reset()
                            except Exception:
                                pass
                        if self.on_speech_ended:
                            self.on_speech_ended()

                    # Case C: Open-mic follow-up window expired without any speech detected
                    elif self.is_follow_up_mode and not self.has_detected_speech_in_window and now >= self.follow_up_expires:
                        print("[wake_word] Continued-conversation window closed after silence timeout.")
                        self.is_window_active = False
                        self.is_follow_up_mode = False
                        if self.on_follow_up_expired:
                            self.on_follow_up_expired()

            except Exception as e:
                time.sleep(0.1)
