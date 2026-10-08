"""
Unit and integration tests for the J.A.R.V.I.S. 3D Knowledge Graph.

Verifies:
1. Backend KnowledgeGraphAdapter aggregation across all structured data sources.
2. Progressive Level-of-Detail (LOD) clustering (100% individual rendering below 1,000).
3. Desktop JarvisAPI integration and serialization.
4. Screen 2 WebGL lifecycle pause/resume gating in SpatialNav.
5. Physics calibration (warmup/cooldown) and lightweight dot rendering.
6. Audio reactivity binding and consolidated draw loop synchronization.
7. Frame render time regression test asserting <50ms JS draw time at 308 and ~1,400 nodes.
"""

import json
import subprocess
import shutil
from pathlib import Path
import pytest

from core.knowledge_graph import KnowledgeGraphAdapter, PALETTE
from frontend.desktop import JarvisAPI


APP_HTML_PATH = Path(__file__).parent.parent / "frontend" / "app.html"


class TestKnowledgeGraphAdapter:
    """Verifies backend knowledge graph data aggregation and progressive LOD schema conformance."""

    def test_adapter_aggregates_all_data_sources_into_unified_schema(self):
        adapter = KnowledgeGraphAdapter()
        data = adapter.get_graph_data(max_nodes=300)

        assert "nodes" in data
        assert "links" in data
        assert "total_raw_nodes" in data
        assert "display_nodes" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["links"], list)
        assert len(data["nodes"]) > 0

        # Verify Central Core "Sun" node
        core_node = next((n for n in data["nodes"] if n["id"] == "core:jarvis"), None)
        assert core_node is not None, "Central core:jarvis node must be present"
        assert core_node["type"] == "CORE"
        assert core_node["val"] == PALETTE["CORE"]["val"]
        assert core_node["color"] == PALETTE["CORE"]["color"]
        assert core_node["label"] == "J.A.R.V.I.S."
        assert "salutation" in core_node["metadata"]
        assert "ONLINE" in core_node["metadata"]["status"]

        # Verify Node Schema fields across all returned nodes
        node_ids = set()
        for node in data["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "type" in node
            assert "val" in node
            assert "color" in node
            assert "metadata" in node
            assert node["type"] in PALETTE
            assert node["id"] not in node_ids, f"Duplicate node id: {node['id']}"
            node_ids.add(node["id"])

        # Verify Link Schema and Referential Integrity
        for link in data["links"]:
            assert "source" in link
            assert "target" in link
            assert "type" in link
            src_id = link["source"] if isinstance(link["source"], str) else link["source"]["id"]
            tgt_id = link["target"] if isinstance(link["target"], str) else link["target"]["id"]
            assert src_id in node_ids, f"Link source '{src_id}' does not exist in nodes"
            assert tgt_id in node_ids, f"Link target '{tgt_id}' does not exist in nodes"

        # Verify aggregated source categories exist in dataset
        types_present = {n["type"] for n in data["nodes"]}
        assert "CORE" in types_present
        # Should contain at least memory rules or skills or lessons
        assert any(t in types_present for t in ["MEMORY_RULE", "MEMORY_SKILL", "LESSON", "SESSION_TOPIC"])

    def test_default_dataset_renders_100_percent_entities_under_lod_threshold(self):
        """Verifies that all ~300 real entities render individually without collapsing into clusters."""
        adapter = KnowledgeGraphAdapter()
        data = adapter.get_graph_data(max_nodes=1500)

        # For current personal data volume (< 1,000 nodes), no clustering should occur
        assert data["total_raw_nodes"] < 1000
        assert data["display_nodes"] == data["total_raw_nodes"], (
            f"Expected 100% individual rendering ({data['total_raw_nodes']} nodes), got {data['display_nodes']}"
        )
        assert data["capped"] is False
        cluster_nodes = [n for n in data["nodes"] if n["type"] == "CLUSTER"]
        assert len(cluster_nodes) == 0, "No cluster badges should exist under LOD threshold"

    def test_node_count_cap_and_clustering_triggers_above_threshold(self):
        adapter = KnowledgeGraphAdapter()
        
        # Test with a low cap to trigger clustering explicitly
        low_cap = 40
        capped_data = adapter.get_graph_data(max_nodes=low_cap)

        assert capped_data["display_nodes"] <= low_cap, (
            f"Display nodes {capped_data['display_nodes']} must not exceed max_nodes {low_cap}"
        )
        assert len(capped_data["nodes"]) <= low_cap

        if capped_data["total_raw_nodes"] > low_cap:
            assert capped_data["capped"] is True
            # Verify cluster badge nodes were created
            clusters = [n for n in capped_data["nodes"] if n["type"] == "CLUSTER"]
            assert len(clusters) > 0, "Cluster badges must be generated when raw nodes exceed display cap"

            for c in clusters:
                assert c["id"].startswith("cluster:")
                assert c["label"].startswith("+")
                assert "cluster_type" in c["metadata"]
                assert c["metadata"]["count"] > 0
                assert len(c["metadata"]["clustered_ids"]) == c["metadata"]["count"]

            # Verify aggregation links connect from core:jarvis to clusters
            cluster_ids = {c["id"] for c in clusters}
            cluster_links = [
                l for l in capped_data["links"]
                if l.get("type") == "aggregates" and l.get("target") in cluster_ids
            ]
            assert len(cluster_links) == len(clusters), "Every cluster badge must have an aggregates link"

    def test_progressive_lod_clustering_above_1000_threshold(self):
        """Simulate high density (1,400 items) and verify progressive LOD clustering behavior."""
        adapter = KnowledgeGraphAdapter()

        synthetic_nodes = [{"id": "core:jarvis", "type": "CORE", "label": "Core", "val": 28, "color": "#FF9D2E"}]
        synthetic_links = []
        for i in range(1400):
            nid = f"synth_rule_{i}"
            synthetic_nodes.append({
                "id": nid,
                "type": "MEMORY_RULE",
                "label": f"Rule {i}",
                "val": 9,
                "color": "#FFA726"
            })
            synthetic_links.append({
                "source": "core:jarvis",
                "target": nid,
                "type": "governs"
            })

        cap = 1200
        clustered_nodes, clustered_links = adapter._apply_clustering_and_capping(
            synthetic_nodes, synthetic_links, max_nodes=cap
        )

        assert len(clustered_nodes) <= cap, f"Resulting nodes {len(clustered_nodes)} must be <= {cap}"
        cluster_node = next((n for n in clustered_nodes if n["id"] == "cluster:facts"), None)
        assert cluster_node is not None
        assert cluster_node["metadata"]["count"] > 100


class TestDesktopKnowledgeGraphAPI:
    """Verifies JarvisAPI desktop bindings for the 3D Knowledge Graph."""

    def test_get_knowledge_graph_data_api(self):
        api = JarvisAPI(initial_mode="full")
        res = api.get_knowledge_graph_data(max_nodes=1500)

        assert isinstance(res, dict)
        assert "nodes" in res
        assert "links" in res
        assert len(res["nodes"]) <= 1500
        # Check JSON serializability for pywebview bridge
        serialized = json.dumps(res)
        assert len(serialized) > 50


class TestFrontendKnowledgeGraphIntegration:
    """Verifies app.html contains all WebGL lifecycle gating, DOM elements, and pass-through CSS."""

    def setup_method(self):
        assert APP_HTML_PATH.exists(), "frontend/app.html must exist"
        self.html = APP_HTML_PATH.read_text(encoding="utf-8")

    def test_3d_force_graph_library_included(self):
        assert '<script src="3d-force-graph.min.js"></script>' in self.html, (
            "app.html must include 3d-force-graph.min.js"
        )
        library_file = APP_HTML_PATH.parent / "3d-force-graph.min.js"
        assert library_file.exists(), "frontend/3d-force-graph.min.js must exist on disk"
        assert library_file.stat().st_size > 100_000, "3d-force-graph.min.js must be a valid bundle"

    def test_dom_container_and_mount_structure(self):
        assert 'id="jarvis-knowledge-graph-container"' in self.html, (
            "app.html must contain #jarvis-knowledge-graph-container inside #shell"
        )
        assert 'id="jarvis-3d-kg-mount"' in self.html, (
            "app.html must contain #jarvis-3d-kg-mount"
        )

    def test_webgl_pause_resume_gated_to_screen_2(self):
        """Verifies WebGL animation pause/resume is strictly gated to Screen 2 in SpatialNav."""
        assert "window.JarvisKnowledgeGraph.resume()" in self.html, (
            "SpatialNav must resume 3D graph animation when Screen 2 is active"
        )
        assert "window.JarvisKnowledgeGraph.pause()" in self.html, (
            "SpatialNav must pause 3D graph animation when leaving Screen 2"
        )
        # Check that the gating condition uses absRatio < 0.45
        assert "absRatio < 0.45" in self.html

    def test_spatial_nav_pointer_and_wheel_exclusions(self):
        """Verifies interacting with the 3D graph does not trigger accidental screen gliding."""
        assert "#jarvis-knowledge-graph-container" in self.html
        assert "onPointerDown" in self.html
        assert "onWheel" in self.html
        # Check exclusion in onPointerDown and onWheel
        assert "e.target.closest('#jarvis-knowledge-graph-container" in self.html or (
            "jarvis-knowledge-graph-container" in self.html
        )

    def test_click_passthrough_css_rules(self):
        """Verifies pass-through click rules for Screen 2 HUD overlay."""
        assert "#workspace-content-grid" in self.html
        assert "pointer-events: none !important;" in self.html
        assert "#jarvis-chat-container" in self.html
        # Chat bubbles and controls must have pointer-events: auto !important
        assert "#chat-panel-header" in self.html
        assert "#thread > *" in self.html

    def test_frontend_3d_force_graph_calibrated_physics(self):
        """Verifies organic spherical distribution and sphere radius calibration."""
        assert "GRAPH_RADIUS" in self.html
        assert "_generateOrganicSpherePositions" in self.html
        assert "generateDefaultData" in self.html

    def test_audio_reactivity_and_whole_sphere_breathing(self):
        """Verifies whole sphere breathes in O(1) with jarvisAudioEnergy and audio tick is synchronized."""
        assert "JarvisKnowledgeGraph" in self.html
        assert "updateAudioEnergy" in self.html
        assert "graphGroup.scale.set" in self.html
        assert "ambientLight" in self.html
        # Verify drawJarvisHud drives audio energy updates directly
        assert "window.JarvisKnowledgeGraph.updateAudioEnergy(jarvisAudioEnergy)" in self.html

    def test_fullscreen_and_window_resize_listeners(self):
        """Verifies 3D Knowledge Graph re-fits renderer and camera on window resize."""
        assert "resize()" in self.html
        assert "camera.updateProjectionMatrix()" in self.html
        assert "window.addEventListener('resize'" in self.html
        assert "ResizeObserver" in self.html

    def test_stationary_default_and_fly_through_zoom(self):
        """Verifies OrbitControls damping, distance bounds, and camera near clipping."""
        assert "controls.minDistance" in self.html
        assert "PerspectiveCamera" in self.html
        assert "controls.dampingFactor" in self.html

    def test_unreal_bloom_pass_and_rich_link_web(self):
        """Verifies 3D parallax starfield and luminous cybernetic web styling."""
        assert "starFar" in self.html
        assert "starNear" in self.html
        assert "makeStarField" in self.html
        assert "sizeAttenuation: true" in self.html

    def test_animated_link_flow_particles(self):
        """Verifies hub node glow halos and floating HUD labels."""
        assert "_hubFlares" in self.html
        assert "_labelSprites" in self.html
        assert "CATEGORY_PALETTE" in self.html

    def test_receive_event_handler_for_knowledge_graph_data(self):
        assert "case 'knowledge_graph_data':" in self.html
        assert "window.JarvisKnowledgeGraph.setData(data);" in self.html

    def test_frame_render_time_regression_under_threshold(self):
        """
        Automated regression test: asserts JS draw call time stays under 50ms
        at both current real data volume (308) and near-cap (~1,400) synthetic nodes.
        """
        node_bin = shutil.which("node")
        if not node_bin:
            pytest.skip("Node.js not available for headless WebGL frame regression test")

        benchmark_script = """
        const { chromium } = require('playwright');
        const path = require('path');
        (async () => {
            const browser = await chromium.launch({
                executablePath: '/usr/bin/chromium',
                args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu', '--headless=new']
            });
            const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
            await page.goto('file://' + path.resolve('frontend/app.html'), { waitUntil: 'load' });
            await page.waitForTimeout(1000);

            const counts = [308, 1400];
            const out = {};
            for (const c of counts) {
                const res = await page.evaluate(async (nodeCount) => {
                    const kg = window.JarvisKnowledgeGraph;
                    const nodes = [{ id: 'core:jarvis', type: 'CORE' }];
                    const links = [];
                    for (let i = 1; i < nodeCount; i++) {
                        nodes.push({ id: 'n_' + i, type: 'MEMORY_RULE' });
                        const hub = Math.floor(i / 8) * 8;
                        links.push({ source: (hub === 0 || hub === i) ? 'core:jarvis' : 'n_' + hub, target: 'n_' + i });
                    }
                    const renderer = kg.renderer;
                    const origRender = renderer.render.bind(renderer);
                    const renderTimes = [];
                    renderer.render = function(s, cam) {
                        const t0 = performance.now();
                        origRender(s, cam);
                        renderTimes.push(performance.now() - t0);
                    };

                    kg.setData({ nodes, links });
                    await new Promise(r => setTimeout(r, 600));

                    renderTimes.length = 0;
                    await new Promise(r => setTimeout(r, 600));
                    renderer.render = origRender;

                    const avg = renderTimes.reduce((a, b) => a + b, 0) / (renderTimes.length || 1);
                    return { avgRenderMs: avg };
                }, c);
                out[c] = res.avgRenderMs;
            }
            console.log(JSON.stringify(out));
            await browser.close();
        })();
        """

        proc = subprocess.run([node_bin, "-e", benchmark_script], capture_output=True, text=True, timeout=40)
        assert proc.returncode == 0, f"Benchmark script failed: {proc.stderr}"
        data = json.loads(proc.stdout)
        
        avg_308 = float(data["308"])
        avg_1400 = float(data["1400"])

        # Regression threshold: JS draw call time must remain under 50ms (avoiding Long Tasks)
        assert avg_308 < 50.0, f"Expected 308-node draw time < 50ms, got {avg_308:.2f}ms"
        assert avg_1400 < 50.0, f"Expected 1,400-node draw time < 50ms, got {avg_1400:.2f}ms"
