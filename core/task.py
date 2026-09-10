import time
import uuid
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

class TaskState(str, Enum):
    CREATED = "CREATED"
    OPENING = "OPENING"
    RUNNING = "RUNNING"
    UPDATING = "UPDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    MINIMIZED = "MINIMIZED"

class TaskType(str, Enum):
    GOOGLE_SEARCH = "google_search"
    YOUTUBE_SEARCH = "youtube_search"
    BROWSER_SURF = "browser_surf"
    SYSTEM_ACTION = "system_action"
    TERMINAL_COMMAND = "terminal_command"
    MEMORY_RECALL = "memory_recall"
    INVESTIGATION = "investigation"
    FLIGHT_INTEL = "flight_intel"
    WEATHER_INTEL = "weather_intel"
    TRAFFIC_INTEL = "traffic_intel"
    MAPS_NAV = "maps_nav"
    GENERIC_SKILL = "generic_skill"

@dataclass
class TaskFinding:
    title: str
    url: str = ""
    snippet: str = ""
    source: str = "web"
    confidence: float = 0.95
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class Task:
    task_id: str = field(default_factory=lambda: f"sb-{uuid.uuid4().hex[:6]}")
    type: str = TaskType.GENERIC_SKILL.value
    title: str = "Agent Task"
    state: str = TaskState.CREATED.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress_pct: int = 0
    progress_msg: str = "Initializing task..."
    summary: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    findings: List[TaskFinding] = field(default_factory=list)
    selected_index: Optional[int] = None
    is_minimized: bool = False

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "task_id": self.task_id,
            "type": self.type,
            "title": self.title,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress_pct": self.progress_pct,
            "progress_msg": self.progress_msg,
            "summary": self.summary,
            "data": self.data,
            "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.findings],
            "selected_index": self.selected_index,
            "is_minimized": self.is_minimized,
        }
        return res
