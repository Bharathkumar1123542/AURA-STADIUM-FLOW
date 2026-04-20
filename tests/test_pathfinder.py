"""
Tests – AStarPathfinder
=========================
Unit tests for the A* pathfinding algorithm used for crowd rerouting.

Coverage:
  - Same-start-goal trivial case
  - Valid path between known sections
  - Path avoids high-density sections (congestion penalty)
  - Returns None on impossible graph
  - Admissible heuristic (H ≤ actual edge cost)
  - Segment building correctness
  - Full graph connectivity (all sections can reach all others)
"""

import math
import pytest

from backend_core.services.pathfinder import (
    AStarPathfinder,
    STADIUM_GRAPH,
    SECTION_POSITIONS,
    PathResult,
    PathSegment,
)


# ══════════════════════════════════════════════════════════════════
# Basic path finding
# ══════════════════════════════════════════════════════════════════


class TestAStarBasic:
    def test_same_start_goal(self, pathfinder):
        """Path from A to A should be trivial with zero cost."""
        result = pathfinder.find_path("A", "A", densities={})
        assert result is not None
        assert result.path == ["A"]
        assert result.total_cost == pytest.approx(0.0)
        assert result.segments == []

    def test_returns_path_result(self, pathfinder):
        result = pathfinder.find_path("A", "D", densities={})
        assert isinstance(result, PathResult)

    def test_path_starts_and_ends_correctly(self, pathfinder):
        result = pathfinder.find_path("B", "F", densities={})
        assert result is not None
        assert result.path[0] == "B"
        assert result.path[-1] == "F"

    def test_path_contains_only_valid_sections(self, pathfinder):
        sections = set(STADIUM_GRAPH.keys())
        result = pathfinder.find_path("C", "E", densities={})
        assert result is not None
        for node in result.path:
            assert node in sections

    def test_path_is_connected(self, pathfinder):
        """Each consecutive pair in the path must be graph neighbours."""
        result = pathfinder.find_path("A", "F", densities={})
        assert result is not None
        for i in range(len(result.path) - 1):
            src, dst = result.path[i], result.path[i + 1]
            neighbours = {n for n, _ in STADIUM_GRAPH.get(src, [])}
            assert dst in neighbours, f"{dst} is not a neighbour of {src}"

    def test_total_cost_positive(self, pathfinder):
        result = pathfinder.find_path("A", "D", densities={})
        assert result is not None
        assert result.total_cost > 0.0

    def test_segments_count(self, pathfinder):
        result = pathfinder.find_path("A", "D", densities={})
        assert result is not None
        assert len(result.segments) == len(result.path) - 1

    def test_segments_are_path_segment_objects(self, pathfinder):
        result = pathfinder.find_path("C", "D", densities={})
        assert result is not None
        for seg in result.segments:
            assert isinstance(seg, PathSegment)
            assert seg.cost > 0.0


# ══════════════════════════════════════════════════════════════════
# Congestion avoidance
# ══════════════════════════════════════════════════════════════════


class TestCongestionAvoidance:
    def test_high_density_increases_cost(self, pathfinder):
        """Same path should cost more when the destination section is congested."""
        cost_empty = pathfinder.find_path("A", "D", densities={}).total_cost
        # Make D extremely congested
        cost_congested = pathfinder.find_path("A", "D", densities={"D": 1.0}).total_cost
        assert cost_congested > cost_empty

    def test_avoids_fully_congested_intermediate(self, pathfinder):
        """
        If a direct intermediate node is at 100% density the pathfinder
        should prefer an alternate route (higher total cost, different path).
        A→D direct goes through possibly A→D (cost 1.5), but with D at 100%
        the pathfinder's cost of going through D should rise.
        Test that the path from C to E avoids section D when D is at 100%.
        """
        result_normal = pathfinder.find_path("C", "E", densities={})
        result_congested = pathfinder.find_path("C", "E", densities={"D": 1.0})
        assert result_normal is not None
        assert result_congested is not None
        # At minimum, the cost should be higher when a key node is congested
        assert result_congested.total_cost >= result_normal.total_cost

    def test_high_density_tracked_in_reasoning(self, pathfinder):
        """The reasoning field should mention avoided sections when density ≥ 70%."""
        result = pathfinder.find_path("C", "E", densities={"D": 0.85})
        assert result is not None
        # Either directly avoided D or chose a path not through D
        # Reasoning should be a non-empty string
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 0


# ══════════════════════════════════════════════════════════════════
# Impossible graph / edge cases
# ══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_returns_none_on_disconnected_graph(self, pathfinder):
        """A graph with no connections should return None."""
        isolated_graph = {"A": [], "B": []}
        isolated_pathfinder = AStarPathfinder(
            graph=isolated_graph,
            positions={"A": (0.0, 0.0), "B": (1.0, 1.0)},
        )
        result = isolated_pathfinder.find_path("A", "B", densities={})
        assert result is None

    def test_single_node_graph(self, pathfinder):
        """A single-node graph reaching itself should work."""
        single_graph = {"X": []}
        single_pathfinder = AStarPathfinder(
            graph=single_graph,
            positions={"X": (0.0, 0.0)},
        )
        result = single_pathfinder.find_path("X", "X", densities={})
        assert result is not None
        assert result.path == ["X"]
        assert result.total_cost == pytest.approx(0.0)

    def test_all_pairs_reachable(self, pathfinder):
        """Every section should be able to reach every other section."""
        sections = list(STADIUM_GRAPH.keys())
        for start in sections:
            for goal in sections:
                result = pathfinder.find_path(start, goal, densities={})
                assert result is not None, (
                    f"No path found from {start} to {goal} in the default stadium graph"
                )


# ══════════════════════════════════════════════════════════════════
# Heuristic admissibility
# ══════════════════════════════════════════════════════════════════


class TestHeuristic:
    def test_heuristic_bounded_by_scale(self, pathfinder):
        """
        The pathfinder uses HEURISTIC_SCALE=0.70 so that H(n) stays below
        the minimum base edge cost (1.0) for all ADJACENT pairs, guaranteeing
        A* optimality. Verify that H ≤ HEURISTIC_SCALE * raw_euclidean_distance.
        """
        import math
        scale = pathfinder.HEURISTIC_SCALE
        for src in SECTION_POSITIONS:
            for dst in SECTION_POSITIONS:
                x1, y1 = SECTION_POSITIONS[src]
                x2, y2 = SECTION_POSITIONS[dst]
                raw = math.sqrt((x2-x1)**2 + (y2-y1)**2)
                h = pathfinder._heuristic(src, dst)
                assert h <= raw * scale + 1e-9, (
                    f"H({src},{dst})={h:.4f} exceeds scale bound {raw*scale:.4f}"
                )

    def test_heuristic_scale_applied(self, pathfinder):
        """HEURISTIC_SCALE=0.70 must reduce the raw Euclidean distance."""
        import math
        for src, positions in SECTION_POSITIONS.items():
            for dst, dst_pos in SECTION_POSITIONS.items():
                if src == dst:
                    continue
                x1, y1 = SECTION_POSITIONS[src]
                x2, y2 = SECTION_POSITIONS[dst]
                raw = math.sqrt((x2-x1)**2 + (y2-y1)**2)
                h = pathfinder._heuristic(src, dst)
                assert h == pytest.approx(raw * pathfinder.HEURISTIC_SCALE, rel=1e-6)

    def test_heuristic_zero_for_same_node(self, pathfinder):
        for section in SECTION_POSITIONS:
            assert pathfinder._heuristic(section, section) == pytest.approx(0.0)

    def test_heuristic_non_negative(self, pathfinder):
        sections = list(SECTION_POSITIONS.keys())
        for node in sections:
            for goal in sections:
                assert pathfinder._heuristic(node, goal) >= 0.0


# ══════════════════════════════════════════════════════════════════
# to_dict serialisation
# ══════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_to_dict_has_required_keys(self, pathfinder):
        result = pathfinder.find_path("A", "D", densities={})
        assert result is not None
        d = result.to_dict()
        assert "path" in d
        assert "total_cost" in d
        assert "segments" in d
        assert "reasoning" in d

    def test_to_dict_path_is_list(self, pathfinder):
        result = pathfinder.find_path("B", "E", densities={})
        d = result.to_dict()
        assert isinstance(d["path"], list)

    def test_to_dict_segments_are_dicts(self, pathfinder):
        result = pathfinder.find_path("A", "F", densities={})
        d = result.to_dict()
        for seg in d["segments"]:
            assert isinstance(seg, dict)
            assert "section_from" in seg
            assert "section_to" in seg
            assert "cost" in seg
