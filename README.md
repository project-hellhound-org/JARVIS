<p align="center">
  <img src="assets/jarvis-banner.png" alt="J.A.R.V.I.S." width="100%"/>
</p>

<h1 align="center">J.A.R.V.I.S. : AUTONOMOUS TACTICAL PERSONAL & OSINT ASSISTANT</h1>
<p align="center">
  <b>Just A Rather Very Intelligent System — Autonomous Personal AI Assistant by Project Hellhound</b>
  <br>
  <i>Your all-in-one local personal assistant & OSINT intelligence powerhouse. From daily workflow automation, live web search, system task execution, and tactical voice interaction to target enumeration, identity pivoting, multi-source username scans, email intelligence, and 2D/3D topology graphs.</i>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#installation--setup">Installation & Setup</a> ·
  <a href="#ai-model-routing">AI Routing</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#capabilities--arsenal">Capabilities</a> ·
  <a href="#wake-word--voice">Voice & Wake Word</a> ·
  <a href="#desktop-gui-app">Desktop GUI</a> ·
  <a href="#license">License</a>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python Version"/></a>
  <a href="https://github.com/project-hellhound-org/SOLDIER-BOY/releases"><img src="https://img.shields.io/badge/Release-v2.0.0-cyan?style=flat-square" alt="Release Version"/></a>
  <img src="https://img.shields.io/badge/AI--Powered-Ollama%20%7C%20NVIDIA%20NIM%20%7C%20Gemini-red?style=flat-square" alt="AI Support"/>
  <img src="https://img.shields.io/badge/Voice-Fish%20Audio%20%7C%20Local%20Clone-brightgreen?style=flat-square" alt="Voice Synthesis"/>
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-lightgrey?style=flat-square" alt="Platform"/>
  <img src="https://img.shields.io/badge/License-GPLv3-blue?style=flat-square" alt="License"/>
</p>

---

> [!NOTE]
> ### 🛡️ Persona & Protocol Notice
> **J.A.R.V.I.S.** operates as your tactical executive assistant and intelligence officer. He manages daily queries, web research, system automation, and open-source intelligence gathering with an **intelligent, witty, articulate, and highly capable demeanor**. All operations run locally or through encrypted API bridges with zero data leaving your machine without explicit authorization.

---

## What Is This?

**J.A.R.V.I.S.** is the next-generation autonomous tactical personal AI assistant and OSINT intelligence framework developed by **Project Hellhound**. Transitioned into an **all-in-one personal assistant**, J.A.R.V.I.S. handles everything—from everyday task automation, live web dorking, and system query resolution to deep target investigations, username cross-referencing, email metadata parsing, and network topology graphing.

It features **persistent memory and an automated case blackboard**—all personal tasks, research logs, discovered handles, email profiles, DNS records, IP locations, Wayback snapshots, and evidence notes are retained in isolated workspaces (`~/.jarvis/cases/<target>/` and `data/jarvis_memory.json`) so your assistant workflows seamlessly resume across sessions.

Operate across three flexible interfaces:
- **Desktop GUI Application**: A dedicated PyWebView/FastAPI desktop interface featuring collapsible card modules, transparent eye API key toggles, 2D/3D radial topology graphs, and hands-free microphone input (`jarvis` or `soldierboy`).
- **Interactive Terminal**: An interactive terminal environment with real-time monologue streaming, live progress feedback, and inline command execution (`jarvis --cli`).
- **Headless CLI Runner**: Direct one-line command execution for automated scripts and headless task triggers (`jarvis investigate <target>`).

---

## How It Works

```
You ──> jarvis ("Jarvis...") ──> Intent & Task Parser ──> Executive Assistant Core
                                         │                           │
                                         ▼                           ▼
                              Personal Workflow Engine    OSINT & Recon Toolchain
                              ├─ Live Web Research        ├─ Sherlock & Maigret
                              ├─ System Automation        ├─ Holehe & Gravatar
                              ├─ Tactical Voice Synthesis ├─ GitHub & Breach Check
                              └─ Case Memory Blackboard   └─ EXIF, WHOIS & DNS
                                         │
                                         ▼ (Populates Persistent Blackboard)
                              Jarvis Voice Engine <────── Grounding Guard
                                         │                 (Fish Audio / Local)
                                         ▼
                              /export (Submission-Ready HTML Case & Task Reports)
```

- **All-in-One Personal Capabilities**: Ask general questions, perform live web extractions, summarize complex documents, run system audits, or launch target investigations.
- **Collapsible Card System**: Settings, ledgers, and maps render in responsive card components with collapsible header toggles (`▼` / `▶`) and real-time state badges.
- **Eye Toggle Security**: API key fields (Gemini, NVIDIA NIM, Fish Audio) feature transparent eye buttons (`👁`) for password visibility control.
- **Hands-Free Speech Input**: Direct voice interaction paired with push-to-talk and intelligent conversational streaming.
- **Interactive Topology Graphs**: 2D SVG radial network fallback + 3D WebGL graph populating entity connections alongside active skill nodes.

---

## AI Model Routing & Recommended Providers

Autonomous intelligence gathering and personal assistant reasoning utilize fast SLMs and frontier LLMs for intent parsing and monologue synthesis.

> [!TIP]
> ### 💡 Recommended AI Engines
> - **NVIDIA NIM (Strongly Recommended for Cloud)**: Use **NVIDIA NIM** (`meta/llama-3.3-70b-instruct` or `nvidia/nemotron-3-super-120b-a12b`). It is **instant, ultra-fast**, and provides generous free API credits for frontier-class 70B–120B reasoning at zero cost.
> - **Local Qwen (Strongly Recommended for Offline)**: Run **Qwen** (`qwen2.5:7b` or `qwen2.5:14b`) locally via Ollama. Delivers exceptional local reasoning, structured output adherence, and 100% offline privacy with zero data leaving your machine.

---

## 🎙️ Voice Intelligence & Synthesis Engine

J.A.R.V.I.S. features high-fidelity, low-latency conversational speech powered by **Fish Audio** (Voice ID: `05b36da8574341d0803391491850db20`) with automatic phrase caching and zero-shot local fallback (**Chatterbox TTS**).

### 🎧 Conversational Audio Protocol
- **Fish Audio Voice Engine**: Configured out of the box with J.A.R.V.I.S. voice signature `05b36da8574341d0803391491850db20`.
- **Pre-synthesized Tactical Audio Cache**: Instant vocal responses for common system acknowledgments and telemetry updates.
- **Muting Guard**: Ambient speech listening is automatically muted while J.A.R.V.I.S. is speaking to prevent self-triggering audio loops.

---

## Installation & Setup

### 1. Requirements & Prerequisites
- **Operating System**: Linux (Ubuntu, Debian, Kali, Arch), macOS.
- **Python**: Version 3.10 or higher.
- **Dependencies**: `ffmpeg`, `espeak-ng` (optional for local voice dataset generation).

### 2. Fast Deploy

#### Option A: One-Line Remote Installer
```bash
curl -fsSL https://raw.githubusercontent.com/project-hellhound-org/SOLDIER-BOY/main/install.sh | bash
```

#### Option B: Standard Git Clone
```bash
git clone https://github.com/project-hellhound-org/SOLDIER-BOY.git
cd SOLDIER-BOY

# Initialize config from template
cp config.yaml.example config.yaml

# Run system installer
chmod +x install.sh
./install.sh
```

#### Reload Shell Environment
```bash
source ~/.bashrc   # or source ~/.zshrc
```

The automated installer will:
- Set up an isolated Python environment (`soldier-env`).
- Initialize `config.yaml` from template configuration.
- Install OSINT and personal assistant toolchains (`sherlock`, `maigret`, `holehe`, `chatterbox-tts`).
- Mount desktop application dependencies (`pywebview`).
- Register global `jarvis` and `soldierboy` command integration.

---

## Quick Start

### 1. Native Desktop GUI App
Launch the desktop application with collapsible cards and interactive 2D/3D topology graph:
```bash
jarvis
# (Legacy alias also supported: soldierboy)
```

### 2. Interactive Terminal
Launch the interactive CLI interface:
```bash
jarvis --cli
```

### 3. Headless Direct Command
Run a direct target investigation or assistant command:
```bash
jarvis investigate target@email.com
jarvis investigate johndoe_87
jarvis investigate target.com
```

---

## Commands

All actions can be triggered via slash commands or natural language:

### Core Commands

| Command | Aliases | Description | Usage |
| :--- | :--- | :--- | :--- |
| `investigate` | `/investigate`, `stalk` | Run multi-pass OSINT sweep against a target | `investigate <target>` |
| `resume` | `/resume`, `load` | Open saved investigation case or task log | `resume <target>` |
| `pivot` | `/pivot`, `focus` | Pivot investigation on a discovered handle or IP | `pivot <entity>` |
| `cases` | `/cases`, `list` | List all archived cases and saved sessions | `cases` |
| `notes` | `/notes`, `add` | Append investigative note or task reminder | `notes <text>` |
| `export` | `/export`, `report` | Generate offline HTML investigation & task report | `export` |
| `help` | `/?`, `info` | Display command guide and available modules | `help` |
| `exit` | `quit` | Exit workspace | `exit` |

---

## Capabilities & Arsenal

J.A.R.V.I.S. combines personal assistant capabilities with specialized OSINT tools:

| Module | Category | Description |
| :--- | :--- | :--- |
| `personal_assistant` | Workflow Core | General Q&A, system commands, document analysis, and daily task management. |
| `voice_engine` | Audio Engine | Low-latency Fish Audio synthesis (`05b36da8574341d0803391491850db20`) with local caching. |
| `local_voice_clone` | Audio Fallback | Zero-shot local voice synthesis powered by Chatterbox TTS. |
| `sherlock` | Username Scan | Multi-source account discovery across 300+ platforms. |
| `maigret` | Deep Identity | Advanced profile harvesting and metadata extraction. |
| `holehe` | Email Intelligence | Checks registration status across 120+ web services. |
| `gravatar` | Profile & Avatar | Public profile image, display name, and bio extraction. |
| `github_recon` | Code Intel | Repository search, commit history, and README profile parsing. |
| `dns_whois` | Domain Recon | Pulls A, AAAA, MX, TXT, CNAME records, and WHOIS registration. |
| `wayback` | History Snapshots | Discovers archived URLs and historical domain snapshots. |
| `exif_geo` | Image Metadata | Extracts GPS coordinates and camera metadata from image files. |
| `breach_directory` | Leak Inspection | Checks exposed data breaches and compromised field types. |
| `abuse_ipdb` | IP Reputation | Evaluates IP threat score, ISP, and proxy/VPN status. |
| `2d_3d_graph` | Network Topology | Guaranteed 2D SVG radial fallback + 3D WebGL relationship graph. |

---

## Scope Policy & Legal Compliance

J.A.R.V.I.S. is built for **personal productivity, authorized penetration testing, bug bounty programs, CTF challenges, and legitimate security research**.
- Always obtain explicit authorization before investigating external targets.
- Users are solely responsible for ensuring compliance with all applicable local and international laws.

## License

This project is licensed under the [GNU General Public License v3 (GPLv3)](LICENSE).

---

## Acknowledgements & Inspirations

- **[God's Eye View](https://github.com/bilawalsidhu/gods-eye-view)** by Bilawal Sidhu (MIT License): Inspiring tactical airspace tracking, 3D trajectory traces, atmospheric weather telemetry, and real-time agentic loop methodologies. Tactical models and telemetry datasets are adapted and credited under their respective open-source licenses.

---

## Author

<p align="center">
  <a href="https://l4zz3rj0d.github.io">
    <img src="https://img.shields.io/badge/Founder-L4ZZ3RJ0D-c0392b?style=for-the-badge" alt="L4ZZ3RJ0D"/>
  </a>
</p>

<div align="center">
  <br/>
  <sub>Built with precision and intelligence. At your service, sir.</sub>
</div>

