import unittest

from task_diagram import render, validate


class TaskDiagramTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "title": "A & B",
            "nodes": [
                {"id": "read", "label": "Read <files>", "lane": "human", "human_step": True},
                {"id": "write", "label": "Write answer", "output": "draft"},
            ],
            "edges": [{"from": "read", "to": "write"}],
        }

    def test_renders_escaped_node_markup_and_controls(self):
        html = render(self.spec)
        self.assertIn('id="node-read" data-node-id="read"', html)
        self.assertIn('data-node-label="Read &lt;files&gt;"', html)
        self.assertIn("Read &lt;files&gt;", html)
        self.assertIn("lane: human · human step: True", html)
        self.assertIn('class="diagram-container"', html)
        self.assertIn("window.Archify.view", html)
        self.assertIn("zoomIn", html)
        self.assertIn("pointerdown", html)

    def test_rejects_missing_and_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "node ids"):
            validate({"nodes": [{"label": "missing"}], "edges": []})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate({"nodes": [{"id": "x"}, {"id": "x"}], "edges": []})

    def test_rejects_unknown_dependencies_and_cycles(self):
        with self.assertRaisesRegex(ValueError, "unknown node"):
            validate({"nodes": [{"id": "x"}], "edges": [{"from": "x", "to": "y"}]})
        cycle = {"nodes": [{"id": "x"}, {"id": "y"}], "edges": [{"from": "x", "to": "y"}, {"from": "y", "to": "x"}]}
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate(cycle)
        cycle["edges"][1]["rework"] = True
        self.assertTrue(validate(cycle))

    def test_rejects_graph_over_limit(self):
        with self.assertRaisesRegex(ValueError, "60-node"):
            validate({"nodes": [{"id": str(i)} for i in range(61)], "edges": []})

    def test_enforces_titles_labels_ids_and_nonempty_graph(self):
        with self.assertRaisesRegex(ValueError, "at least one node"):
            validate({"nodes": [], "edges": []})
        for invalid in (
            {"nodes": [{"id": "Upper"}], "edges": []},
            {"nodes": [{"id": "ok", "label": " "}], "edges": []},
            {"nodes": [{"id": "ok", "label": "x" * 121}], "edges": []},
            {"title": "x" * 201, "nodes": [{"id": "ok"}], "edges": []},
            {"title": " ", "nodes": [{"id": "ok"}], "edges": []},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate(invalid)

    def test_rejects_malformed_edges_and_non_boolean_rework(self):
        for edges in (
            [{"from": [], "to": "ok"}],
            [{"from": "ok", "to": "ok", "rework": "yes"}],
            [None],
        ):
            with self.subTest(edges=edges):
                with self.assertRaises(ValueError):
                    validate({"nodes": [{"id": "ok"}], "edges": edges})

    def test_render_is_byte_for_byte_deterministic(self):
        self.assertEqual(render(self.spec).encode(), render(self.spec).encode())


if __name__ == "__main__":
    unittest.main()
