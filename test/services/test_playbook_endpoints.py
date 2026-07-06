import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine

import app.db.session as db_session
from app.services import playbook


class TestPlaybookEndpoints(unittest.TestCase):
    """
    docs/DECISIONS_V3.md §3: Settings UI needs to view/edit/disable bullets
    and roll back versions - exercised here at the HTTP layer since that's
    the actual contract the frontend depends on.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

        from fastapi.testclient import TestClient

        from app.asgi import app

        self.client = TestClient(app)

    def tearDown(self):
        db_session.engine = self._original_engine

    def test_list_playbooks_returns_only_active_versions(self):
        playbook.create_version("creative_director", "fun_facts", [{"text": "v1", "enabled": True}])
        playbook.create_version("creative_director", "fun_facts", [{"text": "v2", "enabled": True}])

        response = self.client.get("/api/v1/playbooks")
        self.assertEqual(response.status_code, 200)
        playbooks = response.json()["data"]["playbooks"]
        self.assertEqual(len(playbooks), 1)
        self.assertEqual(playbooks[0]["version"], 2)
        self.assertEqual(playbooks[0]["bullets"], [{"text": "v2", "enabled": True}])

    def test_list_playbooks_empty_when_none_exist(self):
        response = self.client.get("/api/v1/playbooks")
        self.assertEqual(response.json()["data"]["playbooks"], [])

    def test_get_versions_returns_full_history_newest_first(self):
        playbook.create_version("creative_director", "fun_facts", [{"text": "v1", "enabled": True}])
        playbook.create_version("creative_director", "fun_facts", [{"text": "v2", "enabled": True}])

        response = self.client.get(
            "/api/v1/playbooks/versions", params={"agent": "creative_director", "content_type_id": "fun_facts"}
        )
        versions = response.json()["data"]["versions"]
        self.assertEqual([v["version"] for v in versions], [2, 1])

    def test_update_bullet_disables_it_and_creates_new_version(self):
        pb = playbook.create_version(
            "creative_director", "fun_facts", [{"text": "a", "enabled": True}, {"text": "b", "enabled": True}]
        )
        response = self.client.patch(f"/api/v1/playbooks/{pb.id}/bullets/1", json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["bullets"][1]["enabled"], False)

    def test_update_bullet_unknown_playbook_returns_404(self):
        response = self.client.patch("/api/v1/playbooks/999999/bullets/0", json={"enabled": False})
        self.assertEqual(response.status_code, 404)

    def test_rollback_reactivates_a_prior_version(self):
        first = playbook.create_version("creative_director", "fun_facts", [{"text": "v1", "enabled": True}])
        playbook.create_version("creative_director", "fun_facts", [{"text": "v2", "enabled": True}])

        response = self.client.post(f"/api/v1/playbooks/{first.id}/rollback")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["bullets"], [{"text": "v1", "enabled": True}])
        self.assertEqual(playbook.get_active_bullets("creative_director", "fun_facts"), ["v1"])

    def test_rollback_unknown_playbook_returns_404(self):
        response = self.client.post("/api/v1/playbooks/999999/rollback")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
