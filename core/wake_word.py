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

class WakeWordEngine:
    def __init__(
        self,
        on_wake_detected: Optional[Callable[[str], None]] = None,
        on_speech_ended: Optional[Callable[[], None]] = None,
        audio_chunk_callback: Optional[Callable[[bytes], None]] = None,
        model_path: Optional[str] = None,
        threshold: float = 0.35,
        silence_timeout_sec: float = 0.8,
        max_window_sec: float = 12.0
    ):
        self.on_wake_detected = on_wake_detected
        self.on_speech_ended = on_speech_ended
        self.audio_chunk_callback = audio_chunk_callback

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

        self._init_engine(model_path)

    def reset_cooldown(self, seconds: float = 2.0):
        """End active capture and suppress wake detection for the cooldown period."""
        self.is_window_active = False
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

        try:
            import openwakeword
            from openwakeword.model import Model
            if target_model and os.path.exists(target_model):
                print(f"[wake_word] Loading custom openWakeWord model: {target_model}")
                self._model = Model(wakeword_models=[target_model], inference_framework="onnx")
            else:
                # Built-in or fallback listener for JARVIS
                self._model = None
        except Exception as e:
            print(f"[wake_word] Notice: openWakeWord model init: {e}. Active via Web Speech STT.")
            self._model = None

        # 2. Check microphone hardware
        try:
            import pyaudio
            self._pyaudio = pyaudio.PyAudio()
            device_count = self._pyaudio.get_device_count()
            has_input = False
            for i in range(device_count):
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
        except Exception as e:
            print(f"[wake_word] Audio hardware init notice: {e}. Falling back to Web Speech API.")
            self.fallback_to_web_speech = True

    def start(self):
        """Start background microphone listening thread if hardware is present."""
        if not self.hardware_mic_available or not self._model:
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
        Check incoming raw STT transcription against registered JARVIS wake aliases.
        Returns (matched: bool, phrase: str)
        """
        if not text:
            return False, ""
        clean = text.strip().lower()
        aliases = [
            "jarvis", "hey jarvis", "yo jarvis", "ok jarvis", "okay jarvis", "jarv", "hi jarvis"
        ]

        for alias in aliases:
            if clean.startswith(alias):
                return True, alias.title()
        return False, ""

    def trigger_wake_event(self, trigger_phrase: str = "Hey JARVIS"):
        """Programmatically trigger a wake detection event (e.g. from STT regex fallback)."""
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

                # 2. Run Voice Activity Detection (VAD) for active command window
                if self.is_window_active:
                    elapsed = now - self.window_start_time
                    rms = self._compute_rms(data)

                    if rms > 180.0:
                        self.last_speech_time = now
                        self.has_detected_speech_in_window = True

                    silence_duration = now - self.last_speech_time

                    if (self.has_detected_speech_in_window and silence_duration >= self.silence_timeout_sec) or (elapsed >= self.max_window_sec):
                        self.is_window_active = False
                        self._wake_cooldown_until = time.time() + 2.0
                        if self._model:
                            try:
                                self._model.reset()
                            except Exception:
                                pass
                        if self.on_speech_ended:
                            self.on_speech_ended()

            except Exception as e:
                time.sleep(0.1)
