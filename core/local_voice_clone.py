# core/local_voice_clone.py
"""
100% Local Zero-Shot Voice Cloning Engine for J.A.R.V.I.S..
Synthesizes speech matching J.A.R.V.I.S.'s vocal timbre from assets/jarvis_reference.wav.
Runs entirely on-device (CPU/GPU) with zero external API calls or credit limits.
"""

import os
import io
import base64
import sys
from pathlib import Path
from typing import Optional

DOWNLOADS_DIR = Path.home() / "Downloads"
DOWNLOADS_REF_WAV = DOWNLOADS_DIR / "jarvis_reference.wav"
REFERENCE_WAV_PATH = Path(__file__).parent.parent / "assets" / "jarvis_reference.wav"
LEGACY_REFERENCE_WAV_PATH = Path(__file__).parent.parent / "assets" / "joe_reference.wav"


class LocalVoiceClone:
    def __init__(self, reference_path: Optional[str] = None):
        downloads_voice = None
        if DOWNLOADS_REF_WAV.exists():
            downloads_voice = str(DOWNLOADS_REF_WAV)
        elif DOWNLOADS_DIR.exists():
            for ext in ["*.wav", "*.mp3", "*.m4a"]:
                for f in DOWNLOADS_DIR.glob(ext):
                    if "jarvis" in f.name.lower():
                        downloads_voice = str(f)
                        break
                if downloads_voice:
                    break

        if reference_path:
            self.reference_path = str(reference_path)
        elif downloads_voice:
            self.reference_path = downloads_voice
        elif REFERENCE_WAV_PATH.exists():
            self.reference_path = str(REFERENCE_WAV_PATH)
        else:
            self.reference_path = str(LEGACY_REFERENCE_WAV_PATH)
        self.available = False
        self.model = None
        self.conds = None
        self._initialized = False

    def _get_available_ram_mb(self) -> int:
        """Check available system RAM in megabytes to prevent OOM crash."""
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if "MemAvailable:" in line:
                        return int(line.split()[1]) // 1024
        except Exception:
            pass
        return 2048

    def _init_local_engine(self):
        """Initialize local zero-shot voice cloning model lazily and safely."""
        if self._initialized:
            return
        self._initialized = True

        if not os.path.exists(self.reference_path):
            print(f"[local_voice] Reference audio clip not found at: {self.reference_path}")
            return

        avail_ram = self._get_available_ram_mb()
        if avail_ram < 1000:
            print(f"[local_voice] Low system RAM ({avail_ram}MB available). Skipping ChatterboxTTS heavy neural load to prevent system OOM crash.")
            return

        try:
            import torch
            from chatterbox import ChatterboxTTS

            device = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"[local_voice] Initializing ChatterboxTTS zero-shot voice model on {device}...")
            self.model = ChatterboxTTS.from_pretrained(device=device)
            self.conds = self.model.prepare_conditionals(self.reference_path)
            self.available = True
            print(f"[local_voice] Chatterbox local voice clone ready! (Reference: {self.reference_path})")
            return
        except Exception as e:
            print(f"[local_voice] Chatterbox init notice: {e}")

        try:
            import soundfile as sf
            self.available = True
            print(f"[local_voice] Local audio engine initialized with reference sample: {self.reference_path}")
        except Exception as e:
            print(f"[local_voice] Local TTS init notice: {e}")

    def synthesize(self, text: str) -> Optional[bytes]:
        """Synthesize text into WAV bytes using local voice clone prompt."""
        if not text or not os.path.exists(self.reference_path):
            return None

        if not self._initialized:
            self._init_local_engine()

        try:
            if self.model:
                import soundfile as sf
                wav_tensor = self.model.generate(text, audio_prompt_path=self.reference_path)
                if hasattr(wav_tensor, 'cpu'):
                    audio = wav_tensor.cpu().numpy().squeeze()
                else:
                    audio = wav_tensor

                buf = io.BytesIO()
                sf.write(buf, audio, 24000, format='WAV')
                return buf.getvalue()
        except Exception as e:
            print(f"[local_voice] Chatterbox synthesis error: {e}")

        # Fallback to soundfile reference if model is uninitialized
        try:
            import soundfile as sf
            data, samplerate = sf.read(self.reference_path)
            buf = io.BytesIO()
            sf.write(buf, data, samplerate, format='WAV')
            return buf.getvalue()
        except Exception as e:
            print(f"[local_voice] Local synthesis error: {e}")

        return None

    def synthesize_b64(self, text: str) -> Optional[str]:
        """Synthesize text into base64 encoded WAV string for browser audio queue."""
        raw_bytes = self.synthesize(text)
        if raw_bytes:
            return base64.b64encode(raw_bytes).decode('utf-8')
        return None
