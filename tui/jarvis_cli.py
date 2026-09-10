# tui/jarvis_cli.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import asyncio
import re
import threading
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from core.orchestrator import Orchestrator
from core.target_model import Target, Entity
from core.case_brief import CaseBrief, parse_brief_with_slm, plan_investigation
from narrative.jarvis_voice import JarvisVoice
from narrative.session_memory import SessionMemory
from memory.lessons_store import LessonsStore

console = Console()

C_GOLD   = "#e8a020"
C_RED    = "#e63b1f"
C_LIGHT  = "#f0dcc8"
C_GREEN  = "#3aad50"
C_ORANGE = "#e05520"
C_DIM    = "#9a8070"
C_ACCENT = "#ff4422"

BANNER = r"""
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
"""

# ── False-positive command patterns ──────────────────────────────
_FP_PATTERNS = [
    re.compile(r"^false\s*positive[:\s]+(.+)", re.IGNORECASE),
    re.compile(r"^fp[:\s]+(.+)", re.IGNORECASE),
    re.compile(r"^no\s+that'?s?\s+fake\s*(.*)", re.IGNORECASE),
    re.compile(r"^that'?s?\s+(a\s+)?false\s*positive\s*(.*)", re.IGNORECASE),
]


def print_banner():
    console.print()
    for line in BANNER.splitlines():
        console.print(f"  [bold {C_ACCENT}]{line}[/]")
    console.print()
    console.print(f'  [{C_GOLD}]  "I notice everything."[/]')
    console.print(f'  [{C_DIM}]  OSINT Investigator — zero APIs, fully local[/]')
    console.print()
    console.print(f"  [{C_DIM}]  investigate · resume · cases · pivot · notes · export · help · exit[/]")
    console.print()


def print_status(msg: str):
    console.print(f"  [{C_DIM}]  ›  {msg}[/]")


def print_found(entity: Entity):
    plat = f"  [{C_GREEN}]{entity.platform}[/]" if entity.platform else ""
    verified = entity.metadata.get("verified")
    v_tag = ""
    if verified is True:
        v_tag = f"  [{C_GREEN}]✓verified[/]"
    elif verified is False:
        v_tag = f"  [{C_ORANGE}]✗unverified[/]"
    console.print(
        f"  [{C_GREEN}]  ✓[/]  [bold {C_LIGHT}]{entity.value}[/]{plat}{v_tag}"
    )


def print_breach(entity: Entity):
    plat = f"  [{C_ORANGE}]{entity.platform}[/]" if entity.platform else ""
    console.print(
        f"  [{C_ORANGE}]  ⚠[/]  [bold {C_ORANGE}]{entity.value}[/]{plat}"
    )


def print_jarvis_quote(quote: str):
    """Print an in-character quote panel."""
    console.print(Panel(
        f"[italic grey70]\"{quote}\"[/italic grey70]",
        border_style="bright_blue",
        title="[bold blue]J.A.R.V.I.S.[/bold blue]",
        expand=False
    ))

print_joe_quote = print_jarvis_quote


def print_findings(target: Target):
    console.print()
    console.print(f"  [{C_ACCENT}]  {'─' * 58}[/]")
    console.print(f"  [bold {C_LIGHT}]  FINDINGS[/]")
    console.print(f"  [{C_ACCENT}]  {'─' * 58}[/]")
    console.print()

    for e in target.entities:
        plat = f"  [{C_DIM}]({e.platform})[/]" if e.platform else ""
        verified = e.metadata.get("verified")
        v_tag = ""
        if verified is True:
            v_tag = f"  [{C_GREEN}]✓[/]"
        elif verified is False:
            v_tag = f"  [{C_ORANGE}]✗[/]"
        console.print(
            f"  [{C_DIM}]  {e.entity_type:<16}[/]"
            f"[{C_LIGHT}]{e.value}[/]{plat}{v_tag}"
        )

    if target.breaches:
        console.print()
        for b in target.breaches:
            fields = ", ".join(b.exposed_fields[:3])
            console.print(
                f"  [{C_ORANGE}]  {'breach':<16}[/]"
                f"[bold {C_ORANGE}]{b.name}[/]"
                f"  [{C_DIM}]{b.date}  {fields}[/]"
            )

    console.print()
    filled = int(target.risk_score * 30)
    bar = (
        f"[{C_ACCENT}]{'█' * filled}[/]"
        f"[{C_DIM}]{'░' * (30 - filled)}[/]"
    )
    console.print(
        f"  [{C_DIM}]  {'risk score':<16}[/]{bar}  "
        f"[bold {C_ACCENT}]{target.risk_score:.2f}[/]"
    )
    console.print()
    console.print(f"  [{C_ACCENT}]  {'─' * 58}[/]")
    console.print()


def print_monologue(text: str):
    from rich.text import Text
    from rich.padding import Padding

    console.print()
    console.print(f"  [{C_ACCENT}]  ┌─ jarvis {'─' * 43}[/]")
    console.print()

    for line in text.strip().splitlines():
        if line.strip():
            # Wrap long lines cleanly at 70 chars
            words = line.split()
            current = ""
            for word in words:
                if len(current) + len(word) + 1 > 70:
                    console.print(f"  [{C_GOLD}]  {current}[/]")
                    current = word
                else:
                    current = f"{current} {word}".strip()
            if current:
                console.print(f"  [{C_GOLD}]  {current}[/]")
        else:
            console.print()

    console.print()
    console.print(f"  [{C_ACCENT}]  └{'─' * 50}[/]")
    console.print()


def show_help():
    console.print()
    console.print(f"  [bold {C_LIGHT}]  COMMANDS[/]")
    console.print()
    for cmd, desc in [
        ("investigate <target>",  "start new investigation (email, domain, IP, username)"),
        ("  --brief \"...\"",  "  add context hints for smarter recon"),
        ("resume <target>",  "load and continue a saved case"),
        ("pivot  <entity>",  "investigate a newly discovered entity"),
        ("cases",            "list all saved investigations"),
        ("notes  <text>",    "add a note to the current case"),
        ("export",           "export current case as HTML report"),
        ("fp <platform>",    "mark last finding as false positive (learns for future)"),
        ("  --ctf / --pentest", "  tag context for the lesson"),
        ("help",             "show this"),
        ("exit",             "leave"),
    ]:
        console.print(
            f"  [bold {C_ACCENT}]  {cmd:<26}[/][{C_DIM}]{desc}[/]"
        )
    console.print(
        f"\n  [{C_DIM}]  Or type anything — J.A.R.V.I.S. will answer from investigation context.[/]\n"
    )


def list_cases():
    from core.target_model import CASES_DIR
    cases = list(CASES_DIR.glob("*/case.json"))
    if not cases:
        console.print(f"\n  [{C_DIM}]  No saved cases yet.[/]\n")
        return
    console.print()
    for p in cases:
        console.print(f"  [{C_ACCENT}]  ·[/]  [{C_LIGHT}]{p.parent.name}[/]")
    console.print()


def get_prompt() -> str:
    try:
        sys.stdout.write("\033[38;2;230;59;31m  jarvis ›\033[0m  ")
        sys.stdout.flush()
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        console.print(f'\n\n  [{C_GOLD}]  "Goodbye, Sir."[/]\n')
        sys.exit(0)


def _parse_stalk_args(arg: str):
    """
    Parse 'target --brief "context text" --ctf' from investigate command.
    Returns (target_str, brief_text, context).
    """
    brief_text = None
    context = "general"

    # Extract --brief "..." or --brief '...'
    brief_match = re.search(r'--brief\s+["\'](.+?)["\']', arg)
    if brief_match:
        brief_text = brief_match.group(1)
        arg = arg[:brief_match.start()] + arg[brief_match.end():]

    # Extract context flags
    if "--ctf" in arg:
        context = "ctf"
        arg = arg.replace("--ctf", "")
    elif "--pentest" in arg:
        context = "pentest"
        arg = arg.replace("--pentest", "")

    target_str = arg.strip()
    return target_str, brief_text, context


def _parse_fp_command(text: str):
    """
    Parse false-positive commands. Returns (platform, context) or (None, None).
    Matches: 'false positive: Pinterest', 'fp Pinterest --ctf', 'no that's fake'
    """
    for pattern in _FP_PATTERNS:
        m = pattern.match(text.strip())
        if m:
            remainder = m.group(m.lastindex or 1).strip() if m.lastindex else ""

            # Extract context flags
            context = "general"
            if "--ctf" in remainder:
                context = "ctf"
                remainder = remainder.replace("--ctf", "").strip()
            elif "--pentest" in remainder:
                context = "pentest"
                remainder = remainder.replace("--pentest", "").strip()

            platform = remainder.strip() if remainder.strip() else None
            return platform, context

    return None, None


class JarvisCLI:
    def __init__(self, initial_target: str = None):
        self.initial_target = initial_target
        self.current_target: Target = None
        self.voice = JarvisVoice()
        self.memory = SessionMemory()
        self.lessons_store = LessonsStore()
        self.last_entity: Entity = None  # Track last finding for fp command
        self.orch = Orchestrator(
            on_status=self._on_status,
            on_find=self._on_find,
            on_done=self._on_done,
            lessons_store=self.lessons_store,
        )

    def run(self):
        print_banner()
        if self.initial_target:
            self._investigate(self.initial_target)
        self._loop()

    def _loop(self):
        while True:
            text = get_prompt()
            if not text:
                continue
            parts = text.split(" ", 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ("exit", "quit", "q"):
                console.print(f'\n  [{C_GOLD}]  "Until next time."[/]\n')
                sys.exit(0)
            elif cmd in ("investigate", "stalk") and arg:
                self._investigate(arg)
            elif cmd == "pivot" and arg:
                self._investigate(arg)
            elif cmd == "resume" and arg:
                self._resume(arg)
            elif cmd == "cases":
                list_cases()
            elif cmd == "notes" and arg and self.current_target:
                self.current_target.notes.append(arg)
                self.current_target.save()
                console.print(f"\n  [{C_GREEN}]  Note saved.[/]\n")
            elif cmd == "export" and self.current_target:
                self._export()
            elif cmd == "help":
                show_help()
            else:
                # Check for false-positive commands before passing to J.A.R.V.I.S.
                fp_platform, fp_context = _parse_fp_command(text)
                if fp_platform is not None or any(p.match(text.strip()) for p in _FP_PATTERNS):
                    self._handle_false_positive(fp_platform, fp_context)
                else:
                    self._ask(text)

    def _investigate(self, target_input: str):
        target_str, brief_text, context = _parse_stalk_args(target_input)
        if not target_str:
            console.print(f"\n  [{C_ORANGE}]  No target specified.[/]\n")
            return

        # Parse brief if provided
        brief = None
        plan = None
        if brief_text:
            console.print(f"\n  [{C_DIM}]  analyzing context and planning strategy...[/]")
            from core.input_parser import parse
            parsed_input = parse(target_str)
            plan = plan_investigation(brief_text, target_str, parsed_input.target_type)
            if plan.reasoning:
                print_jarvis_quote(plan.reasoning)
            
            # If plan did not yield hints, fall back to default parser
            if not plan.extra_hints:
                brief = parse_brief_with_slm(brief_text)
                if brief.hints:
                    console.print(f"  [{C_GREEN}]  Brief parsed: {', '.join(brief.hints.keys())}[/]")
                else:
                    console.print(f"  [{C_DIM}]  No structured hints extracted — proceeding with baseline.[/]")
            else:
                console.print(f"  [{C_GREEN}]  Strategy plan parsed: {', '.join(plan.extra_hints.keys())}[/]")

        console.print()
        console.print(f"  [{C_ACCENT}]  ── investigating: [bold]{target_str}[/bold] ──[/]")
        console.print()

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.current_target = loop.run_until_complete(
                self.orch.investigate(target_str, brief=brief, plan=plan)
            )
            loop.close()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join()

    def _resume(self, target: str):
        try:
            self.current_target = Target.load(target)
            console.print(f"\n  [{C_GREEN}]  Resumed: {target}[/]\n")
        except FileNotFoundError:
            console.print(f"\n  [{C_ORANGE}]  No case found for: {target}[/]\n")

    def _ask(self, question: str):
        if not self.current_target or not self.current_target.entities:
            console.print(f"\n  [{C_DIM}]  thinking...[/]")
            result = self.voice.chat(question, None)
        else:
            console.print(f"\n  [{C_DIM}]  thinking...[/]")
            result = self.voice.chat(question, self.current_target)

        self.memory.add("user", question)
        self.memory.add("jarvis", result["text"])

        if result.get("rate_limited"):
            console.print(f"\n  [{C_ORANGE}]  ⚠ Rate limited — using local SLM[/]")

        print_monologue(result["text"])

    def _handle_false_positive(self, platform: str, context: str = "general"):
        """Record a false-positive lesson for future investigations."""
        # If no platform specified, try to use the last entity's platform
        if not platform and self.last_entity:
            platform = self.last_entity.platform

        if not platform:
            console.print(f"\n  [{C_ORANGE}]  Which platform? Usage: fp <platform> [--ctf|--pentest][/]\n")
            return

        trigger = f"{platform} username profile claimed to exist but was a false positive"
        lesson = f"{platform} gives false positives — lower confidence for future hits on this platform"

        success = self.lessons_store.add_lesson(
            trigger=trigger,
            lesson=lesson,
            platform=platform,
            context=context,
        )

        if success:
            console.print()
            console.print(f"  [{C_GREEN}]  ✓ Lesson stored for {platform} [{context}][/]")
            console.print(f"  [{C_GOLD}]  \"I won't make that mistake again.\"[/]")
            console.print()
        else:
            console.print(f"\n  [{C_DIM}]  Lesson store unavailable — install sentence-transformers and chromadb.[/]\n")

    def _export(self):
        from exporters.html_report import generate
        path = generate(self.current_target)
        console.print(f"\n  [{C_GREEN}]  Report saved: {path}[/]\n")

    async def _on_status(self, msg: str):
        print_status(msg)

    async def _on_find(self, entity: Entity, target: Target):
        self.last_entity = entity  # Track for fp command
        if entity.entity_type == "breach":
            print_breach(entity)
        else:
            print_found(entity)

    async def _on_done(self, target: Target):
        print_findings(target)
        console.print(f"  [{C_DIM}]  composing...[/]")
        result = self.voice.closing_monologue(target)
        if result.get("rate_limited"):
            console.print(f"  [{C_ORANGE}]  ⚠ Gemini rate limited — using local SLM for monologue[/]")
        self.memory.add("jarvis", result["text"])
        print_monologue(result["text"])


# Backward-compatible alias


def run(initial_target: str = None):
    JarvisCLI(initial_target=initial_target).run()