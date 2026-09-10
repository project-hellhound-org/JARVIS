<p align="center">
  <img src="assets/jarvis-banner.png" alt="J.A.R.V.I.S." width="100%"/>
</p>

<h1 align="center">J.A.R.V.I.S. : AUTONOMOUS TACTICAL OSINT SPECIALIST & INTELLIGENCE SYSTEM</h1>
<p align="center">
  <b>Just A Rather Very Intelligent System — Autonomous OSINT Specialist & Intelligence Framework by Project Hellhound</b>
  <br>
  <i>An authoritative, multi-source OSINT intelligence powerhouse and tactical assistant. Equipped with deep target enumeration, multi-platform username scans (300+ services), identity pivoting, email verification, domain/WHOIS mapping, historical archives, breach telemetry, real-time ADS-B military flight tracking, keyless atmospheric telemetry, and 2D/3D topology graphing. J.A.R.V.I.S. doesn't just collect data—he cross-correlates, verifies, and delivers actionable, grounded investigative intelligence.</i>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#installation--setup">Installation & Setup</a> ·
  <a href="#the-osint-specialist-arsenal">OSINT Arsenal</a> ·
  <a href="#ai-model-routing">AI Routing</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#voice-engine">Voice Engine</a> ·
  <a href="#desktop-gui-app">Desktop GUI</a> ·
  <a href="#license">License</a>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python Version"/></a>
  <a href="https://github.com/project-hellhound-org/JARVIS/releases"><img src="https://img.shields.io/badge/Release-v2.0.0--OSINT.Specialist-cyan?style=flat-square" alt="Release Version"/></a>
  <img src="https://img.shields.io/badge/Specialization-Autonomous%20OSINT%20Specialist-blueviolet?style=flat-square" alt="Specialization"/>
  <img src="https://img.shields.io/badge/AI--Powered-Ollama%20%7C%20NVIDIA%20NIM%20%7C%20Gemini-red?style=flat-square" alt="AI Support"/>
  <img src="https://img.shields.io/badge/Voice-Fish%20Audio%20%7C%20Local%20Clone-brightgreen?style=flat-square" alt="Voice Synthesis"/>
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-lightgrey?style=flat-square" alt="Platform"/>
  <img src="https://img.shields.io/badge/License-GPLv3-blue?style=flat-square" alt="License"/>
</p>

---

> [!NOTE]
> ### 🛡️ Authoritative OSINT Specialist & Intelligence Protocol
> **J.A.R.V.I.S.** operates as an elite, autonomous **OSINT specialist and intelligence officer**. Drawing upon dozens of cross-referenced intelligence sources—from global domain infrastructure and social registries to real-time ADS-B airspace feeds and breach archives—he verifies and correlates findings before drawing conclusions. His analysis is delivered with **refined British eloquence, dry wit, unflappable composure, and absolute factual grounding**.

---

## What Is This?

**J.A.R.V.I.S.** is an autonomous tactical OSINT specialist and personal intelligence framework developed by **Project Hellhound**. Built from the ground up for cybersecurity researchers, bug bounty hunters, CTF competitors, and intelligence operators, J.A.R.V.I.S. consolidates every phase of open-source reconnaissance into an automated, interactive workflow.

### Why J.A.R.V.I.S. is an Authoritative OSINT Source
Unlike passive scrapers or noisy search wrappers, J.A.R.V.I.S. acts as a true intelligence analyst:
- **Multi-Vector Entity Pivoting**: Transition seamlessly from an email address to a username, discover associated GitHub repositories, parse commit histories for developer identities, extract PGP keys, and uncover domain infrastructure.
- **Strict Anti-Hallucination Grounding**: All verbal debriefs, monologues, and dossier exports are bound 100% to verified scan data recorded on the persistent blackboard. Zero fabricated usernames, zero invented endpoints.
- **Persistent Case Blackboard**: Every target investigation creates an isolated, auditable workspace (`cases/<target>/` and `data/jarvis_memory.json`) storing raw API outputs, normalized entity graphs, timestamped evidence notes, and exportable HTML dossiers.
- **Aerial & Infrastructure Telemetry**: Integrated real-time military flight tracking (ADS-B), atmospheric weather intelligence, and public surveillance capabilities inspired by *God's Eye View*.

Operate across three flexible interfaces:
- **Desktop GUI Application**: A dedicated PyWebView/FastAPI desktop interface featuring collapsible card modules, transparent eye API key toggles, 2D/3D radial topology graphs, and hands-free microphone input (`jarvis`).
- **Interactive Terminal**: An interactive terminal environment with real-time monologue streaming, live progress feedback, and inline command execution (`jarvis --cli`).
- **Headless CLI Runner**: Direct one-line command execution for automated scripts and headless task triggers (`jarvis investigate <target>`).

---

## How It Works

```
You ──> jarvis ("Investigate target@domain.com") ──> Intent & Task Parser ──> Tactical Core
                                                            │                     │
                                                            ▼                     ▼
                                                 Personal Workflow       OSINT Recon Toolchain
                                                 ├─ Live Web Dorking     ├─ Sherlock & Maigret
                                                 ├─ Terminal Automation  ├─ Holehe & Gravatar
                                                 ├─ Tactical Telemetry   ├─ GitHub & Breach Checks
                                                 └─ Case Memory Core     └─ EXIF, WHOIS & DNS
                                                            │
                                                            ▼ (Correlates to Persistent Blackboard)
                                                 Jarvis Voice Engine <── Verification Guard
                                                            │            (Fish Audio / Local)
                                                            ▼
                                                 /export (Submission-Ready HTML Dossier)
```

- **All-in-One OSINT Capabilities**: Deep target enumeration, domain profiling, breach checking, and system task automation.
- **Collapsible Card System**: Settings, ledgers, and telemetry render in responsive card components with collapsible header toggles (`▼` / `▶`) and real-time status badges.
- **Eye Toggle Security**: API key fields (Gemini, NVIDIA NIM, Fish Audio) feature transparent eye buttons (`👁`) for password visibility control.
- **Hands-Free Speech Input**: Direct voice interaction paired with Spacebar push-to-talk and intelligent conversational streaming.
- **Interactive 2D/3D Topology Graphs**: Guaranteed 2D SVG radial network fallback + 3D WebGL graph rendering connected target entities and skills.

---

## The OSINT Specialist Arsenal

J.A.R.V.I.S. equips operators with a comprehensive multi-discipline reconnaissance arsenal:

| Category | Module | Engine / Method | Capabilities |
| :--- | :--- | :--- | :--- |
| **Identity & Accounts** | `sherlock` | HTTP/Async Probe | Account discovery across 300+ social and developer platforms. |
| | `maigret` | Deep Metadata Parse | Identity aggregation, profile harvesting, and cross-site verification. |
| | `holehe` | Mail Endpoint Check | Checks email registration across 120+ web services without notifying targets. |
| | `gravatar` | Hash Lookup | Public avatar, bio, display name, and verified profile extraction. |
| **Code & Developer** | `github_recon` | GitHub Public API | Repository inspection, commit email trails, and SSH/PGP key harvesting. |
| **Domain & Network** | `dns_whois` | Socket / RDAP / DNS | Pulls A, AAAA, MX, TXT, CNAME, NS records, and registrar ownership. |
| | `wayback` | CDX Archive Index | Historic URL enumeration, forgotten subdomains, and historical snapshots. |
| **Media & Geo** | `exif_geo` | PIL / EXIF Parser | Extracts GPS coordinates, altitude, camera model, and timestamp metadata. |
| **Threat & Reputation** | `abuse_ipdb` | Threat Intelligence | Evaluates IP threat scores, ISP classification, and proxy/VPN exit nodes. |
| | `breach_directory` | Credential Telemetry | Discovers compromised account breaches and exposed data field classes. |
| **Aerial & Telemetry** | `adsb_radar` | Keyless ADS-B | Real-time military flight tracking, altitude, callsigns, and squawk codes. |
| | `atmospheric_intel` | Open-Meteo | Keyless real-time weather telemetry, cloud cover, and barometric pressure. |
| **Visualization** | `2d_3d_topology` | D3 / Three.js | Radial SVG relationship fallback + 3D WebGL entity topology graph. |

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
curl -fsSL https://raw.githubusercontent.com/project-hellhound-org/JARVIS/main/install.sh | bash
```

#### Option B: Standard Git Clone
```bash
git clone https://github.com/project-hellhound-org/JARVIS.git
cd JARVIS

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
- Set up an isolated Python environment (`jarvis-env`).
- Initialize `config.yaml` from template configuration.
- Install OSINT toolchains (`sherlock`, `maigret`, `holehe`, `chatterbox-tts`).
- Mount desktop application dependencies (`pywebview`).
- Register global `jarvis` command integration.

---

## Quick Start

### 1. Native Desktop GUI App
Launch the desktop application with collapsible cards and interactive 2D/3D topology graph:
```bash
jarvis
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
| `export` | `/export`, `report` | Generate offline HTML investigation dossier | `export` |
| `help` | `/?`, `info` | Display command guide and available modules | `help` |
| `exit` | `quit` | Exit workspace | `exit` |

---

## Scope Policy & Legal Compliance

J.A.R.V.I.S. is built for **authorized penetration testing, bug bounty programs, CTF challenges, educational intelligence gathering, and legitimate security research**.
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
  <sub>Built with precision, intelligence, and absolute veracity. At your service, sir.</sub>
</div>
