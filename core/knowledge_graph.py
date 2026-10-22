"""
J.A.R.V.I.S. 3D Knowledge Graph Backend Data Adapter.

Aggregates structured memory (JarvisMemory, LessonsStore), session context
(SessionMemory), and OSINT entities (TaskManager / cases) into a unified
force-directed graph schema. Implements high-density viewport-culling and
node clustering with aggregate badges to keep the 3D physics simulation
bounded and responsive (default display cap: 150 nodes).
"""

import os
import json
import time
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path


# Aesthetic HUD Color Palette for JARVIS Knowledge Graph (Multi-Colored Obsidian Aesthetic)
PALETTE = {
    "CORE": {"color": "#FFB84D", "glow": "rgba(255, 184, 77, 0.90)", "val": 14},
    "MEMORY_RULE": {"color": "#FACC15", "glow": "rgba(250, 204, 21, 0.80)", "val": 7},
    "MEMORY_FACT": {"color": "#F43F5E", "glow": "rgba(244, 63, 94, 0.75)", "val": 7},
    "MEMORY_SKILL": {"color": "#10B981", "glow": "rgba(16, 185, 129, 0.80)", "val": 8},
    "LESSON": {"color": "#C084FC", "glow": "rgba(192, 132, 252, 0.75)", "val": 7},
    "OSINT_TARGET": {"color": "#EF4444", "glow": "rgba(239, 68, 68, 0.90)", "val": 10},
    "OSINT_ENTITY": {"color": "#06B6D4", "glow": "rgba(6, 182, 212, 0.80)", "val": 6},
    "SESSION_TOPIC": {"color": "#FB923C", "glow": "rgba(251, 146, 60, 0.75)", "val": 7},
    "CLUSTER": {"color": "#FFB84D", "glow": "rgba(255, 184, 77, 0.90)", "val": 14},
    "OBSIDIAN_NOTE": {"color": "#818CF8", "glow": "rgba(129, 140, 248, 0.75)", "val": 8},
    "HEURISTIC": {"color": "#2DD4BF", "glow": "rgba(45, 212, 191, 0.75)", "val": 6},
}

HEURISTIC_TEMPLATES = [
    "Sub-Millimeter Audio Filtering",
    "Orbital Keplerian Propagation",
    "Airspace Corridor Surveillance",
    "Vessel AIS Transponder Lattice",
    "Multimodal Visual Tokenization",
    "Autonomous ReAct Decoupling",
    "Tactical HUD Telemetry Sync",
    "Geospatial Vector Projection",
    "Acoustic Room Impulse Isolation",
    "Barge-In Speech Suppressor",
    "Cross-Session Discourse Vector",
    "Zero-Shot Voice Model Kernel",
    "Episodic Memory Consolidation",
    "Spatial Stage Screen Glide Matrix",
    "Thermal Sensor Hotspot Ingestion",
    "Cesium 3D Terrain LOD Engine",
    "Three.js Celestial Shader Buffer",
    "Sentinel Ambient Vital Guard",
    "Low-Latency WebSocket Audio Pipe",
    "Signal Entropy Disambiguation",
    "Kinetic Vector Correlation",
    "Sub-Carrier Telemetry Stream",
    "Neural Matrix Self-Diagnostic",
    "Direct Memory Bus Optimization",
    "High-Altitude Orbital Tracking",
    "Synthesized Insight Cache",
    "Tactical Lock Triangulation",
    "Cold Room Forensic Linker",
    "Distributed Case Telemetry",
    "Heuristic Intent Classifier",
]

LESSON_PALETTES = [
    {"color": "#C084FC", "glow": "rgba(192, 132, 252, 0.85)"}, # Luminous Violet
    {"color": "#38BDF8", "glow": "rgba(56, 189, 248, 0.85)"},  # Electric Cyan
    {"color": "#34D399", "glow": "rgba(52, 211, 153, 0.85)"},  # Emerald Green
    {"color": "#F472B6", "glow": "rgba(244, 114, 182, 0.85)"}, # Neon Magenta
    {"color": "#818CF8", "glow": "rgba(129, 140, 248, 0.85)"}, # Deep Indigo
    {"color": "#2DD4BF", "glow": "rgba(45, 212, 191, 0.85)"},  # Radiant Teal
]


class KnowledgeGraphAdapter:
    """
    Unifying adapter that transforms disparate JARVIS telemetry and memory stores
    into a coherent 3D Knowledge Graph dataset with high-density clustering.
    """

    DEFAULT_MAX_NODES = 1500
    LOD_CLUSTERING_THRESHOLD = 1000

    def __init__(self, cases_dir: Optional[Path] = None):
        self.cases_dir = cases_dir or (Path(__file__).parent.parent / "cases")

    def get_graph_data(self, max_nodes: int = DEFAULT_MAX_NODES) -> Dict[str, Any]:
        """
        Gathers all knowledge sources, builds unified nodes and links, and
        applies progressive level-of-detail (LOD) clustering and capping.
        Renders 100% of individual entities below the 1,000 threshold.
        """
        raw_nodes: List[Dict[str, Any]] = []
        raw_links: List[Dict[str, Any]] = []

        # 1. Central Core "Sun" Node (Root Anchor — J.A.R.V.I.S. Visual Embodiment)
        sal = self._get_salutation()
        core_node = {
            "id": "core:jarvis",
            "label": "J.A.R.V.I.S.",
            "type": "CORE",
            "val": PALETTE["CORE"]["val"],
            "color": PALETTE["CORE"]["color"],
            "glowColor": PALETTE["CORE"]["glow"],
            "metadata": {
                "presence": "J.A.R.V.I.S. Visual Embodiment",
                "status": "ONLINE & CONSCIOUS",
                "identity": "Autonomous Tactical OSINT & Cognitive Assistant",
                "salutation": sal,
            },
        }
        raw_nodes.append(core_node)

        # 2. Gather from JarvisMemory
        mem_nodes, mem_links = self._gather_memory_nodes()
        raw_nodes.extend(mem_nodes)
        raw_links.extend(mem_links)

        # 3. Gather from LessonsStore
        lesson_nodes, lesson_links = self._gather_lessons_nodes()
        raw_nodes.extend(lesson_nodes)
        raw_links.extend(lesson_links)

        # 4. Gather from SessionMemory
        session_nodes, session_links = self._gather_session_nodes()
        raw_nodes.extend(session_nodes)
        raw_links.extend(session_links)

        # 5. Gather from Active OSINT Cases & TaskManager
        osint_nodes, osint_links = self._gather_osint_nodes()
        raw_nodes.extend(osint_nodes)
        raw_links.extend(osint_links)

        # Cross-domain links to form a unified, organic 3D constellation
        rules = [n["id"] for n in raw_nodes if n["type"] == "MEMORY_RULE"]
        skills = [n["id"] for n in raw_nodes if n["type"] == "MEMORY_SKILL"]
        lessons = [n["id"] for n in raw_nodes if n["type"] == "LESSON"]
        topics = [n["id"] for n in raw_nodes if n["type"] == "SESSION_TOPIC"]
        facts = [n["id"] for n in raw_nodes if n["type"] == "MEMORY_FACT"]
        targets = [n["id"] for n in raw_nodes if n["type"] == "OSINT_TARGET"]

        if lessons:
            for idx, lid in enumerate(lessons):
                if rules and idx % 3 == 0:
                    raw_links.append({
                        "source": lid,
                        "target": rules[(idx // 3) % len(rules)],
                        "type": "governed_by",
                        "strength": 0.3,
                        "particles": 0,
                    })
                if skills and idx % 4 == 0:
                    raw_links.append({
                        "source": lid,
                        "target": skills[(idx // 4) % len(skills)],
                        "type": "applies_skill",
                        "strength": 0.3,
                        "particles": 0,
                    })
                if facts and idx % 12 == 0:
                    raw_links.append({
                        "source": lid,
                        "target": facts[(idx // 12) % len(facts)],
                        "type": "synthesizes",
                        "strength": 0.25,
                        "particles": 0,
                    })

        if topics:
            for idx, tid in enumerate(topics):
                if lessons:
                    raw_links.append({
                        "source": tid,
                        "target": lessons[(idx * 11) % len(lessons)],
                        "type": "references",
                        "strength": 0.35,
                        "particles": 1,
                    })
                if skills:
                    raw_links.append({
                        "source": tid,
                        "target": skills[idx % len(skills)],
                        "type": "invokes",
                        "strength": 0.35,
                        "particles": 1,
                    })
                if rules:
                    raw_links.append({
                        "source": tid,
                        "target": rules[(idx * 4) % len(rules)],
                        "type": "governed_by",
                        "strength": 0.25,
                        "particles": 0,
                    })

        if targets:
            for idx, tgt in enumerate(targets):
                if skills:
                    raw_links.append({
                        "source": tgt,
                        "target": skills[(idx * 2) % len(skills)],
                        "type": "analyzed_by",
                        "strength": 0.4,
                        "particles": 1,
                    })
                if rules:
                    raw_links.append({
                        "source": tgt,
                        "target": rules[(idx * 3) % len(rules)],
                        "type": "monitored_by",
                        "strength": 0.3,
                        "particles": 0,
                    })

        # 5.5 Maintain Constant Constellation Density with Replaceable Baseline Placeholders
        target_density = min(max_nodes, 120)
        if len(raw_nodes) < target_density:
            needed = target_density - len(raw_nodes)
            for i in range(needed):
                tpl_idx = i % len(HEURISTIC_TEMPLATES)
                cycle = i // len(HEURISTIC_TEMPLATES)
                suffix = f" {cycle + 1}" if cycle > 0 else ""
                label = f"{HEURISTIC_TEMPLATES[tpl_idx]}{suffix}"
                nid = f"placeholder_node_{i}"
                raw_nodes.append({
                    "id": nid,
                    "label": label,
                    "type": "HEURISTIC",
                    "val": PALETTE["HEURISTIC"]["val"],
                    "color": PALETTE["HEURISTIC"]["color"],
                    "glowColor": PALETTE["HEURISTIC"]["glow"],
                    "metadata": {
                        "is_placeholder": True,
                        "placeholder_slot": i,
                        "description": "Baseline Autonomous Cognitive Protocol",
                    },
                })
                if i % 6 == 0:
                    raw_links.append({
                        "source": "core:jarvis",
                        "target": nid,
                        "type": "governs",
                        "strength": 0.35,
                        "particles": 1,
                    })
                if i > 0:
                    raw_links.append({
                        "source": f"placeholder_node_{i - 1}",
                        "target": nid,
                        "type": "references",
                        "strength": 0.25,
                        "particles": 0,
                    })

        # 6. Apply High-Density Capping & Clustering Strategy
        final_nodes, final_links = self._apply_clustering_and_capping(
            raw_nodes, raw_links, max_nodes=max_nodes
        )

        return {
            "nodes": final_nodes,
            "links": final_links,
            "total_raw_nodes": len(raw_nodes),
            "display_nodes": len(final_nodes),
            "capped": len(raw_nodes) > len(final_nodes),
            "timestamp": time.time(),
        }

    # ── Source Extractors ─────────────────────────────────────────────

    def _get_salutation(self) -> str:
        try:
            from core.jarvis_memory import JarvisMemory
            return JarvisMemory().get_salutation() or "Sir"
        except Exception:
            return "Sir"

    def _gather_memory_nodes(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        nodes = []
        links = []
        try:
            from core.jarvis_memory import JarvisMemory
            mem = JarvisMemory()
            data = mem.load_memory()

            # Rules & Speech Patterns
            patterns = data.get("user_speech_patterns", [])
            for idx, pat in enumerate(patterns):
                nid = f"mem_rule_{idx}"
                clean_lbl = pat if len(pat) <= 32 else pat[:30] + "..."
                nodes.append({
                    "id": nid,
                    "label": clean_lbl,
                    "type": "MEMORY_RULE",
                    "val": PALETTE["MEMORY_RULE"]["val"],
                    "color": PALETTE["MEMORY_RULE"]["color"],
                    "glowColor": PALETTE["MEMORY_RULE"]["glow"],
                    "metadata": {"source": "jarvis_memory", "full_text": pat, "rule_index": idx},
                })
                # Interconnected mesh topology
                if idx < 3:
                    links.append({
                        "source": "core:jarvis",
                        "target": nid,
                        "type": "governs",
                        "strength": 0.6,
                        "particles": 1,
                    })
                if idx > 0:
                    links.append({
                        "source": f"mem_rule_{idx - 1}",
                        "target": nid,
                        "type": "governs",
                        "strength": 0.4,
                        "particles": 0,
                    })
                if idx > 0 and idx % 16 == 0:
                    links.append({
                        "source": f"mem_rule_{idx - 16}",
                        "target": nid,
                        "type": "governs",
                        "strength": 0.3,
                        "particles": 0,
                    })

            # Failures and Lessons (Facts)
            facts = data.get("failures_and_lessons", [])
            for idx, fact in enumerate(facts):
                nid = f"mem_fact_{idx}"
                clean_lbl = fact if len(fact) <= 32 else fact[:30] + "..."
                nodes.append({
                    "id": nid,
                    "label": clean_lbl,
                    "type": "MEMORY_FACT",
                    "val": PALETTE["MEMORY_FACT"]["val"],
                    "color": PALETTE["MEMORY_FACT"]["color"],
                    "glowColor": PALETTE["MEMORY_FACT"]["glow"],
                    "metadata": {"source": "jarvis_memory", "full_text": fact, "fact_index": idx},
                })
                links.append({
                    "source": "core:jarvis",
                    "target": nid,
                    "type": "learned_from",
                    "strength": 0.5,
                    "particles": 0,
                })
                if patterns:
                    links.append({
                        "source": f"mem_rule_{idx % len(patterns)}",
                        "target": nid,
                        "type": "learned_from",
                        "strength": 0.35,
                        "particles": 0,
                    })

            # Learned Skills
            skills = data.get("learned_skills", [])
            for idx, skill in enumerate(skills):
                sname = skill.get("name", f"skill_{idx}")
                nid = f"skill_{sname}"
                nodes.append({
                    "id": nid,
                    "label": sname.replace("_", " ").title(),
                    "type": "MEMORY_SKILL",
                    "val": PALETTE["MEMORY_SKILL"]["val"],
                    "color": PALETTE["MEMORY_SKILL"]["color"],
                    "glowColor": PALETTE["MEMORY_SKILL"]["glow"],
                    "metadata": {
                        "source": "jarvis_memory",
                        "description": skill.get("description", ""),
                        "trigger": skill.get("trigger", ""),
                    },
                })
                # Core links to each skill as primary executive controller
                links.append({
                    "source": "core:jarvis",
                    "target": nid,
                    "type": "governs",
                    "strength": 0.6,
                    "particles": 1,
                })
                if idx > 0:
                    prev_sname = skills[idx - 1].get("name", f"skill_{idx - 1}")
                    links.append({
                        "source": f"skill_{prev_sname}",
                        "target": nid,
                        "type": "governs",
                        "strength": 0.35,
                        "particles": 0,
                    })
                if patterns:
                    links.append({
                        "source": nid,
                        "target": f"mem_rule_{(idx * 15) % len(patterns)}",
                        "type": "governs",
                        "strength": 0.3,
                        "particles": 0,
                    })
        except Exception:
            pass
        return nodes, links

    def _gather_lessons_nodes(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        nodes = []
        links = []
        try:
            from memory.lessons_store import LessonsStore
            store = LessonsStore()
            lessons = getattr(store, "_lessons", [])
            for idx, l in enumerate(lessons):
                trigger = l.get("trigger", f"Lesson {idx}")
                nid = f"lesson_{idx}"
                clean_lbl = trigger if len(trigger) <= 28 else trigger[:26] + "..."
                lp = LESSON_PALETTES[idx % len(LESSON_PALETTES)]
                nodes.append({
                    "id": nid,
                    "label": clean_lbl,
                    "type": "LESSON",
                    "val": PALETTE["LESSON"]["val"],
                    "color": lp["color"],
                    "glowColor": lp["glow"],
                    "metadata": {
                        "source": "lessons_store",
                        "trigger": trigger,
                        "lesson": l.get("lesson", ""),
                        "platform": l.get("platform", "general"),
                        "timestamp": l.get("timestamp", ""),
                    },
                })
                # Regular anchor links from Core Sun to distribute lessons throughout sphere
                if idx % 15 == 0:
                    links.append({
                        "source": "core:jarvis",
                        "target": nid,
                        "type": "learned_from",
                        "strength": 0.55,
                        "particles": 1,
                    })
                # 3D geodesic surface lattice connecting true nearest surface neighbors on Fibonacci sphere
                for step in (5, 8, 13, 21):
                    if idx >= step:
                        links.append({
                            "source": f"lesson_{idx - step}",
                            "target": nid,
                            "type": "learned_from",
                            "strength": 0.28,
                            "particles": 0,
                        })
        except Exception:
            pass
        return nodes, links

    def _gather_session_nodes(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        nodes = []
        links = []
        try:
            from narrative.session_memory import SessionMemory
            sm = SessionMemory()
            history = sm.last_n(16)
            seen_targets = set()
            for idx, msg in enumerate(history):
                target = msg.get("target")
                if target and target.lower() not in seen_targets:
                    seen_targets.add(target.lower())
                    nid = f"osint_target_{len(seen_targets)}"
                    nodes.append({
                        "id": nid,
                        "label": target,
                        "type": "OSINT_TARGET",
                        "val": PALETTE["OSINT_TARGET"]["val"],
                        "color": PALETTE["OSINT_TARGET"]["color"],
                        "glowColor": PALETTE["OSINT_TARGET"]["glow"],
                        "metadata": {"source": "session_memory", "target": target},
                    })
                    links.append({
                        "source": "core:jarvis" if len(seen_targets) == 1 else f"osint_target_{len(seen_targets) - 1}",
                        "target": nid,
                        "type": "investigates",
                        "strength": 0.8,
                        "particles": 2,
                    })

                # Session Topic
                if msg.get("role") == "user":
                    content = msg.get("content", "").strip()
                    if content and len(content) > 3 and not content.startswith("/"):
                        topic_lbl = content if len(content) <= 24 else content[:22] + "..."
                        topic_id = f"topic_{idx}"
                        nodes.append({
                            "id": topic_id,
                            "label": topic_lbl,
                            "type": "SESSION_TOPIC",
                            "val": PALETTE["SESSION_TOPIC"]["val"],
                            "color": PALETTE["SESSION_TOPIC"]["color"],
                            "glowColor": PALETTE["SESSION_TOPIC"]["glow"],
                            "metadata": {"source": "session_memory", "full_prompt": content, "at": msg.get("at", "")},
                        })
                        prev_topic = next((n["id"] for n in reversed(nodes[:-1]) if n["type"] == "SESSION_TOPIC"), None)
                        links.append({
                            "source": prev_topic or "core:jarvis",
                            "target": topic_id,
                            "type": "mentioned_in",
                            "strength": 0.4,
                            "particles": 1,
                        })
        except Exception:
            pass
        return nodes, links

    def _gather_osint_nodes(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        nodes = []
        links = []
        try:
            # Check for case files on disk (Cold Room OSINT graphs)
            if self.cases_dir.exists():
                for case_folder in self.cases_dir.iterdir():
                    if case_folder.is_dir() and not case_folder.name.startswith("."):
                        target_name = case_folder.name
                        target_id = f"case_target_{target_name}"
                        nodes.append({
                            "id": target_id,
                            "label": target_name,
                            "type": "OSINT_TARGET",
                            "val": PALETTE["OSINT_TARGET"]["val"],
                            "color": PALETTE["OSINT_TARGET"]["color"],
                            "glowColor": PALETTE["OSINT_TARGET"]["glow"],
                            "metadata": {"source": "cases_dir", "case": target_name},
                        })
                        links.append({
                            "source": "core:jarvis",
                            "target": target_id,
                            "type": "investigates",
                            "strength": 0.8,
                            "particles": 2,
                        })

                        # Read findings / entities from summary.json if available
                        sum_file = case_folder / "summary.json"
                        if sum_file.exists():
                            with open(sum_file, "r", encoding="utf-8") as f:
                                case_data = json.load(f)
                            entities = case_data.get("entities", [])
                            for e_idx, ent in enumerate(entities[:12]):
                                val_str = str(ent.get("value") or ent.get("name") or ent)
                                eid = f"case_ent_{target_name}_{e_idx}"
                                nodes.append({
                                    "id": eid,
                                    "label": val_str[:22],
                                    "type": "OSINT_ENTITY",
                                    "val": PALETTE["OSINT_ENTITY"]["val"],
                                    "color": PALETTE["OSINT_ENTITY"]["color"],
                                    "glowColor": PALETTE["OSINT_ENTITY"]["glow"],
                                    "metadata": {"source": "cold_room_case", "entity": ent, "case": target_name},
                                })
                                links.append({
                                    "source": target_id,
                                    "target": eid,
                                    "type": "discovered_during",
                                    "strength": 0.5,
                                    "particles": 1,
                                })
        except Exception:
            pass
        return nodes, links

    # ── High-Density Capping & Clustering Strategy ────────────────────

    def _apply_clustering_and_capping(
        self,
        nodes: List[Dict[str, Any]],
        links: List[Dict[str, Any]],
        max_nodes: int
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Enforces a progressive Level-of-Detail (LOD) display cap (default 1,500 nodes).
        Below LOD_CLUSTERING_THRESHOLD (1,000 nodes), 100% of individual entities are
        rendered as dots without any clustering.
        Above the threshold, overflows in lowest-priority categories (session topics,
        then memory rules, then entities, then lessons) are progressively aggregated into
        cluster badge nodes to keep total display nodes <= max_nodes.
        """
        # Deduplicate nodes by id
        node_map: Dict[str, Dict[str, Any]] = {}
        for n in nodes:
            if n["id"] not in node_map:
                node_map[n["id"]] = n

        all_nodes = list(node_map.values())
        # Render 100% of individual entities if within both max_nodes and the LOD clustering threshold
        if len(all_nodes) <= max_nodes and len(all_nodes) <= self.LOD_CLUSTERING_THRESHOLD:
            valid_ids = {n["id"] for n in all_nodes}
            valid_links = [
                l for l in links
                if (l["source"] if isinstance(l["source"], str) else l["source"]["id"]) in valid_ids
                and (l["target"] if isinstance(l["target"], str) else l["target"]["id"]) in valid_ids
            ]
            return all_nodes, valid_links

        # Partition nodes by category
        core_node = node_map.get("core:jarvis")
        targets = [n for n in all_nodes if n["type"] == "OSINT_TARGET"]
        skills = [n for n in all_nodes if n["type"] == "MEMORY_SKILL"]
        lessons = [n for n in all_nodes if n["type"] == "LESSON"]
        entities = [n for n in all_nodes if n["type"] == "OSINT_ENTITY"]
        rules = [n for n in all_nodes if n["type"] == "MEMORY_RULE"]
        facts = [n for n in all_nodes if n["type"] == "MEMORY_FACT"]
        topics = [n for n in all_nodes if n["type"] == "SESSION_TOPIC"]

        # Calculate quotas
        # Core + Targets + Skills have highest visual priority
        preserved_nodes: List[Dict[str, Any]] = []
        if core_node:
            preserved_nodes.append(core_node)

        preserved_nodes.extend(targets[:30])
        preserved_nodes.extend(skills[:25])

        # Available slots remaining for secondary categories (reserving up to 4 for cluster badges)
        available_slots = max(10, max_nodes - len(preserved_nodes) - 4)

        # Lessons are high value knowledge: allocate 45% of available slots
        # Entities: 25%, Rules/Facts: 20%, Topics: 10%
        lesson_quota = min(len(lessons), int(available_slots * 0.45))
        entity_quota = min(len(entities), int(available_slots * 0.25))
        rule_quota = min(len(rules) + len(facts), int(available_slots * 0.20))
        topic_quota = min(len(topics), max(5, available_slots - (lesson_quota + entity_quota + rule_quota)))

        # If any category doesn't use its quota, redistribute remaining slots
        used_slots = lesson_quota + entity_quota + rule_quota + topic_quota
        extra = available_slots - used_slots
        if extra > 0:
            extra_lessons = min(len(lessons) - lesson_quota, extra)
            lesson_quota += extra_lessons
            extra -= extra_lessons
        if extra > 0:
            extra_entities = min(len(entities) - entity_quota, extra)
            entity_quota += extra_entities

        kept_lessons = lessons[:lesson_quota]
        overflow_lessons = lessons[lesson_quota:]

        kept_entities = entities[:entity_quota]
        overflow_entities = entities[entity_quota:]

        kept_rules = (rules + facts)[:rule_quota]
        overflow_rules = (rules + facts)[rule_quota:]

        kept_topics = topics[:topic_quota]
        overflow_topics = topics[topic_quota:]

        preserved_nodes.extend(kept_lessons)
        preserved_nodes.extend(kept_entities)
        preserved_nodes.extend(kept_rules)
        preserved_nodes.extend(kept_topics)

        cluster_nodes: List[Dict[str, Any]] = []
        cluster_links: List[Dict[str, Any]] = []

        # Create Cluster Badges for overflows
        if overflow_lessons:
            cid = "cluster:lessons"
            cluster_nodes.append({
                "id": cid,
                "label": f"+{len(overflow_lessons)} Lessons",
                "type": "CLUSTER",
                "val": PALETTE["CLUSTER"]["val"],
                "color": PALETTE["LESSON"]["color"],
                "glowColor": PALETTE["CLUSTER"]["glow"],
                "metadata": {
                    "cluster_type": "LESSON",
                    "count": len(overflow_lessons),
                    "clustered_ids": [n["id"] for n in overflow_lessons],
                },
            })
            cluster_links.append({
                "source": "core:jarvis",
                "target": cid,
                "type": "aggregates",
                "strength": 0.7,
                "particles": 2,
            })

        if overflow_entities:
            cid = "cluster:entities"
            cluster_nodes.append({
                "id": cid,
                "label": f"+{len(overflow_entities)} Entities",
                "type": "CLUSTER",
                "val": PALETTE["CLUSTER"]["val"],
                "color": PALETTE["OSINT_ENTITY"]["color"],
                "glowColor": PALETTE["CLUSTER"]["glow"],
                "metadata": {
                    "cluster_type": "OSINT_ENTITY",
                    "count": len(overflow_entities),
                    "clustered_ids": [n["id"] for n in overflow_entities],
                },
            })
            cluster_links.append({
                "source": "core:jarvis",
                "target": cid,
                "type": "aggregates",
                "strength": 0.7,
                "particles": 2,
            })

        if overflow_rules:
            cid = "cluster:facts"
            cluster_nodes.append({
                "id": cid,
                "label": f"+{len(overflow_rules)} Rules & Facts",
                "type": "CLUSTER",
                "val": PALETTE["CLUSTER"]["val"],
                "color": PALETTE["MEMORY_RULE"]["color"],
                "glowColor": PALETTE["CLUSTER"]["glow"],
                "metadata": {
                    "cluster_type": "MEMORY_RULE",
                    "count": len(overflow_rules),
                    "clustered_ids": [n["id"] for n in overflow_rules],
                },
            })
            cluster_links.append({
                "source": "core:jarvis",
                "target": cid,
                "type": "aggregates",
                "strength": 0.7,
                "particles": 2,
            })

        if overflow_topics:
            cid = "cluster:topics"
            cluster_nodes.append({
                "id": cid,
                "label": f"+{len(overflow_topics)} History Topics",
                "type": "CLUSTER",
                "val": PALETTE["CLUSTER"]["val"],
                "color": PALETTE["SESSION_TOPIC"]["color"],
                "glowColor": PALETTE["CLUSTER"]["glow"],
                "metadata": {
                    "cluster_type": "SESSION_TOPIC",
                    "count": len(overflow_topics),
                    "clustered_ids": [n["id"] for n in overflow_topics],
                },
            })
            cluster_links.append({
                "source": "core:jarvis",
                "target": cid,
                "type": "aggregates",
                "strength": 0.7,
                "particles": 2,
            })

        if len(preserved_nodes) + len(cluster_nodes) > max_nodes:
            excess = (len(preserved_nodes) + len(cluster_nodes)) - max_nodes
            if excess < len(preserved_nodes):
                preserved_nodes = preserved_nodes[:-excess]
            else:
                preserved_nodes = preserved_nodes[:1]

        final_nodes = preserved_nodes + cluster_nodes
        valid_ids = {n["id"] for n in final_nodes}

        # Keep original links between preserved nodes + add cluster links
        final_links = [
            l for l in links
            if (l["source"] if isinstance(l["source"], str) else l["source"]["id"]) in valid_ids
            and (l["target"] if isinstance(l["target"], str) else l["target"]["id"]) in valid_ids
        ] + cluster_links

        return final_nodes, final_links
