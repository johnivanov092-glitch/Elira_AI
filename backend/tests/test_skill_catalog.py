"""Tests — Skill manifest catalog (P6 Шаг 13)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.skills.catalog import (  # noqa: E402
    SkillManifest,
    discover_skills,
    load_skill_content,
    match_skills_by_trigger,
)


class TestDiscoverSkills(unittest.TestCase):

    def test_returns_list_of_manifests(self):
        skills = discover_skills()
        self.assertIsInstance(skills, list)
        self.assertGreater(len(skills), 0)
        for s in skills:
            self.assertIsInstance(s, SkillManifest)

    def test_manifests_have_required_fields(self):
        for m in discover_skills():
            self.assertTrue(m.id, f"{m} missing id")
            self.assertTrue(m.name, f"{m.id} missing name")
            self.assertTrue(m.description_short, f"{m.id} missing description_short")
            self.assertIsInstance(m.capabilities, list)
            self.assertIsInstance(m.trigger_words, list)

    def test_only_enabled_returned_by_default(self):
        skills = discover_skills(enabled_only=True)
        self.assertTrue(all(s.enabled for s in skills))

    def test_disabled_excluded(self):
        # All built-ins are enabled; verify enabled_only=False returns same or more
        all_skills = discover_skills(enabled_only=False)
        enabled_skills = discover_skills(enabled_only=True)
        self.assertGreaterEqual(len(all_skills), len(enabled_skills))

    def test_no_content_in_manifests(self):
        for m in discover_skills():
            self.assertFalse(hasattr(m, "content"), "content should not be in SkillManifest")

    def test_sorted_alphabetically(self):
        names = [m.name for m in discover_skills()]
        self.assertEqual(names, sorted(names))

    def test_known_skills_present(self):
        ids = {m.id for m in discover_skills()}
        for expected in ("word_excel", "sql_query", "http_api", "screenshot",
                         "web_research", "python_sandbox", "project_patch", "memory"):
            self.assertIn(expected, ids)


class TestLoadSkillContent(unittest.TestCase):

    def test_returns_non_empty_content(self):
        for m in discover_skills():
            content = load_skill_content(m.id)
            self.assertIsNotNone(content, f"{m.id}: content should not be None")
            self.assertTrue(len(content) > 0, f"{m.id}: content should not be empty")

    def test_unknown_skill_returns_none(self):
        self.assertIsNone(load_skill_content("nonexistent_skill_xyz"))

    def test_content_not_in_manifest(self):
        manifests = discover_skills()
        for m in manifests:
            content = load_skill_content(m.id)
            # manifest's description_short ≠ full content
            self.assertNotEqual(m.description_short, content)


class TestMatchSkillsByTrigger(unittest.TestCase):

    def test_web_trigger_matches_web_research(self):
        matches = match_skills_by_trigger("найди информацию в интернете")
        ids = [m.id for m in matches]
        self.assertIn("web_research", ids)

    def test_sql_trigger_matches_sql_query(self):
        matches = match_skills_by_trigger("выполни SQL select из базы данных")
        ids = [m.id for m in matches]
        self.assertIn("sql_query", ids)

    def test_no_matches_for_nonsense(self):
        matches = match_skills_by_trigger("asjdhfkajsdhfkajshdf")
        self.assertEqual(matches, [])

    def test_top_k_limit(self):
        matches = match_skills_by_trigger("файл sql http screenshot word таблица", top_k=2)
        self.assertLessEqual(len(matches), 2)

    def test_case_insensitive(self):
        matches_lower = match_skills_by_trigger("sql")
        matches_upper = match_skills_by_trigger("SQL")
        self.assertEqual([m.id for m in matches_lower], [m.id for m in matches_upper])


class TestSkillCatalogApi(unittest.TestCase):

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.skills_routes import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_catalog_list_returns_skills(self):
        r = self.client.get("/api/skills/catalog")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreater(data["total"], 0)
        self.assertIn("skills", data)
        # Manifests should NOT include content
        for skill in data["skills"]:
            self.assertNotIn("content", skill)

    def test_catalog_detail_returns_content(self):
        r = self.client.get("/api/skills/catalog/word_excel")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["id"], "word_excel")
        self.assertIn("content", data)
        self.assertTrue(len(data["content"]) > 0)

    def test_catalog_detail_not_found(self):
        r = self.client.get("/api/skills/catalog/totally_unknown_skill")
        self.assertEqual(r.status_code, 404)

    def test_match_endpoint(self):
        r = self.client.get("/api/skills/catalog/match?text=sql+запрос")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("matches", data)


if __name__ == "__main__":
    unittest.main()
