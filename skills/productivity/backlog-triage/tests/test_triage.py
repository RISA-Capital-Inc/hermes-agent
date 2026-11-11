import os, sqlite3, tempfile, unittest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from triage_backlog import scan_boards, classify, build_report, digest, COLUMNS

DDL_TASKS = f"CREATE TABLE tasks ({', '.join(c + ' TEXT' for c in COLUMNS)});"
DDL_LINKS = "CREATE TABLE task_links (parent_id TEXT, child_id TEXT);"

def make_board(root, slug, rows, links=()):
    os.makedirs(os.path.join(root, slug), exist_ok=True)
    db = os.path.join(root, slug, "kanban.db")
    con = sqlite3.connect(db)
    con.execute(DDL_TASKS)
    con.execute(DDL_LINKS)
    for r in rows:
        con.execute(f"INSERT INTO tasks ({','.join(COLUMNS)}) VALUES ({','.join('?'*len(COLUMNS))})",
                    [str(r.get(c, "")) for c in COLUMNS])
    for p, c in links:
        con.execute("INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)", (p, c))
    con.commit(); con.close()

def fixture_root():
    root = tempfile.mkdtemp()
    make_board(root, "board-a", [
        {"id": "t_1", "title": "[Human gate] Approve cutover", "assignee": "", "status": "blocked",
         "priority": "0", "block_kind": "needs_input", "block_recurrences": "0"},
        {"id": "t_2", "title": "housekeeping: delete stale branch", "assignee": "dev", "status": "todo",
         "priority": "0", "block_kind": "", "block_recurrences": "0"},
        {"id": "t_3", "title": "Gate 4 — Confidential-memory tenancy", "assignee": "infra", "status": "blocked",
         "priority": "0", "block_kind": "dependency", "block_recurrences": "0"},
    ])
    return root

class TestScan(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.items = scan_boards(self.root)

    def test_reads_all_tasks(self):
        self.assertEqual(len(self.items), 3)

    def test_board_name_attached(self):
        self.assertEqual({i["board"] for i in self.items}, {"board-a"})

    def test_empty_board_skipped(self):
        os.makedirs(os.path.join(self.root, "empty"), exist_ok=True)
        self.assertEqual(len(scan_boards(self.root)), 3)  # no kanban.db -> absent, not corrupt

    def test_open_parents_counted(self):
        make_board(self.root, "board-b", [
            {"id": "p1", "title": "parent", "assignee": "", "status": "running",
             "priority": "0", "block_kind": "", "block_recurrences": "0"},
            {"id": "c1", "title": "child", "assignee": "", "status": "ready",
             "priority": "0", "block_kind": "", "block_recurrences": "0"},
        ], links=[("p1", "c1")])
        items = {i["id"]: i for i in scan_boards(self.root)}
        self.assertEqual(items["c1"]["open_parents"], 1)
        self.assertEqual(items["p1"]["open_parents"], 0)

class TestClassify(unittest.TestCase):
    def k(self, title, status="todo", block_kind="", open_parents=0):
        return classify({"source": "kanban", "title": title, "status": status,
                         "block_kind": block_kind, "open_parents": open_parents,
                         "labels": [], "number": 0, "body": ""})
    def g(self, title, labels):
        return classify({"source": "github", "title": title, "labels": labels,
                         "number": 1, "status": "OPEN", "block_kind": "",
                         "open_parents": 0, "body": ""})

    def test_kanban_needs_input_is_C(self):
        self.assertEqual(self.k("[Human gate] Approve", status="blocked",
                                block_kind="needs_input")["bucket"], "C")
    def test_kanban_dependency_is_D(self):
        self.assertEqual(self.k("Gate 4", status="blocked",
                                block_kind="dependency")["bucket"], "D")
    def test_kanban_open_parent_is_D(self):
        self.assertEqual(self.k("Wait", open_parents=2)["bucket"], "D")
    def test_kanban_housekeeping_is_A(self):
        self.assertEqual(self.k("housekeeping: delete stale branch")["bucket"], "A")
    def test_kanban_readonly_is_A(self):
        self.assertEqual(self.k("Verify backup lane health (read-only)")["bucket"], "A")
    def test_kanban_implement_verify_is_B(self):
        self.assertEqual(self.k("Implement and verify observation adapter")["bucket"], "B")
    def test_kanban_plain_todo_is_B(self):
        self.assertEqual(self.k("Implement adapter")["bucket"], "B")
    def test_github_policy_is_C(self):
        self.assertEqual(self.g("[policy] Codify ssh model", ["policy", "infra"])["bucket"], "C")
    def test_github_risk_high_auth_is_C(self):
        self.assertEqual(self.g("harden auth", ["risk:high", "auth"])["bucket"], "C")
    def test_github_decision_word_is_C(self):
        self.assertEqual(self.g("[decision] Aux routing: pin or inherit?", ["infra"])["bucket"], "C")
    def test_github_housekeeping_is_A(self):
        self.assertEqual(self.g("housekeeping: delete stale lane-test branches",
                                ["housekeeping", "risk:low"])["bucket"], "A")
    def test_github_bug_is_B(self):
        self.assertEqual(self.g("bug: dashboard crashes", ["bug", "risk:med"])["bucket"], "B")
    def test_github_audit_is_B(self):
        self.assertEqual(self.g("Audit 2026-11-07 F-01: update pending",
                                ["audit-2026-11-07"])["bucket"], "B")

class TestReportDigest(unittest.TestCase):
    ITEMS = [
        {"source": "github", "title": "housekeeping: delete zombie branch x", "labels": ["housekeeping", "risk:low"],
         "number": 360, "repo": "atmanalytics", "bucket": "A", "rule": "A: housekeeping"},
        {"source": "kanban", "title": "[Human gate] Approve cutover", "board": "risa-hermes-transition",
         "id": "t_x", "status": "blocked", "block_kind": "needs_input", "bucket": "C",
         "rule": "C: blocked needs_input/capability"},
        {"source": "kanban", "title": "Review PR 2", "board": "atmanalytics", "id": "t_y",
         "status": "review", "block_kind": "", "bucket": "B", "rule": "B: actionable"},
    ]
    def test_report_counts(self):
        md = build_report(self.ITEMS)
        self.assertIn("A (agent-alone): **1**", md)
        self.assertIn("C (human-first): **1**", md)
        self.assertIn("atmanalytics#360", md)
    def test_digest_lists_C_and_review_not_A(self):
        d = digest(self.ITEMS)
        self.assertIn("t_x", d)
        self.assertIn("t_y", d)
        self.assertNotIn("#360", d)

if __name__ == "__main__":
    unittest.main()
