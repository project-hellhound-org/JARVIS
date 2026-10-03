# Project Overview: J.A.R.V.I.S. (Stark-Grade Autonomous AI Partner)

## 1. Project Vision & Concept
**J.A.R.V.I.S.** is a next-generation, voice-first desktop AI companion and operational partner inspired by MARVEL’s J.A.R.V.I.S.. It combines real-time voice interaction, autonomous system skill execution, holographic UI telemetry, dynamic persistent memory, and self-upgrading code introspection into a single desktop application.

Unlike static chat assistants, J.A.R.V.I.S. operates as an active desktop co-pilot: executing local OS tasks, performing live web intelligence, populating holographic visual HUD cards, and auto-tuning its own voice recognition and skill set based on interaction feedback.

---

## 2. Core Architecture & Technology Stack

### A. Frontend & UI System
- **Framework**: PyWebview (Linux GTK / WebKit container) rendering a custom HTML5/CSS3 interface.
- **Visual Design**: Stark-grade holographic HUD featuring dark glassmorphism, dynamic glowing neon elements, reactive audio visualizers, multi-card findings grids, and real-time sentiment intensity badges.
- **Control Modalities**: Voice-first continuous listening with fallback text input, quick skill overlay (`/skills`), and raw JSON telemetry schema toggles.

### B. Voice & Audio Intelligence Pipeline
- **Wake Word Detection**: Local ONNX model via `openWakeWord` ("Jarvis") with automatic fallback to STT regex pattern matching.
- **Voice Activity Detection (VAD)**: Energy-based ambient audio VAD configured with a 2.5-second silence hangover to prevent cutting off natural speech mid-sentence.
- **Speech-to-Text (STT)**: Dual-layer fallback combining Google Speech Recognition with browser Web Speech API.
- **Text-to-Speech (TTS)**: High-fidelity zero-shot voice synthesis via **Fish Audio API** (Voice ID: `e81ae965a9a94ed69ff05eed7e7a57c7`) with fallback to local speech engines.

### C. Language Model & Fast-Path Dispatcher
- **Primary LLM**: NVIDIA NIM (`nvidia/nemotron-3-ultra-550b-a55b`).
- **SLM Fallback**: Local Ollama model (`qwen2.5:3b-instruct-q4_0`).
- **Fast-Path Engine**: Directly intercepts OS commands, app launches, and structured web searches to achieve sub-50ms execution speed, bypassing LLM API roundtrips.

---

## 3. Abilities & System Skills

J.A.R.V.I.S. currently possesses **9 active core skills**, normalized into structured 5-tuple JSON payloads:

1. **`open_app`**: Launches local desktop applications (browser, terminal, text editor, media apps).
2. **`google_search`**: Performs live web searches, extracts clean text snippets, and feeds structured telemetry cards to the UI.
3. **`calendar_intel`**: Monitors schedule, detects double-bookings, sends reminder alerts, and handles meeting rescheduling.
4. **`inbox_intel`**: Scans email/messaging inboxes in read-only mode to generate TL;DR summaries for urgent communications.
5. **`maps_nav`**: Provides live traffic rerouting, location discovery, and nearest route lookup.
6. **`cloud_docs`**: Searches Google Drive & Dropbox file repositories to retrieve document key points.
7. **`smart_home`**: Controls local IoT devices (lights, thermostat, door locks, arrival macros).
8. **`self_upgrade`**: Inspects its own codebase files, logs skill accuracy, performs versioned backups, and auto-retrains wake word sensitivity.
9. **`jarvis_action_hud`**: Populates interactive holographic HUD cards with reliability ratings, sentiment badges, and raw JSON schema toggles.

---

## 4. Self-Learning & Memory Systems

- **Dynamic Persistent Memory (`core/jarvis_memory.py`)**: Automatically registers new skills and updates `data/jarvis_memory.json` during live interactions without requiring manual configuration.
- **Speech Pattern Adaptation**: Tracks user speech habits, acoustic mishears, and preferred response styles.
- **Performance Feedback Loop**: Audits skill execution accuracy (`hits` vs `whiffs`) to optimize tool selection over time.

---

## 5. Purpose & Use Cases ("What It Is For Us")

1. **Hands-Free Desktop Co-Pilot**: Run system actions, search the web, and control applications without switching context or typing.
2. **Live Operational HUD**: View structured search results, target intelligence, and system telemetry in visual pop-up cards.
3. **Self-Improving Companion**: An AI partner that learns from its mistakes, adapts to the user's speech patterns, and upgrades its own memory and scripts over time.

---

## 6. Gaps & Opportunities for Future Upgrades (For ChatGPT / AI Consultation)

To take J.A.R.V.I.S. to full enterprise efficiency, the following areas can be expanded:

1. **Cloud OAuth Integration**: Complete production OAuth 2.0 flows for Google Drive, Dropbox, and Gmail in `modules/cloud_docs.py` and `modules/inbox_intel.py`.
2. **Local Multi-Modal Vision**: Add real-time desktop screen analysis (VLM) so J.A.R.V.I.S. can "see" what the user is working on and offer proactive help.
3. **Proactive Automation & Background Cron**: Enable background cron triggers so J.A.R.V.I.S. actively alerts the user about calendar conflicts, unread urgent emails, or system resource spikes without needing a prompt.
4. **Offline Voice Pipeline**: Replace Web Speech API fallbacks entirely with local Whisper STT and local Chatterbox TTS for 100% offline air-gapped operation.
