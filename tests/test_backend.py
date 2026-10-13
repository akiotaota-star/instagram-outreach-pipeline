import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from outreach.engine import analyse_profile, generate_drafts
from outreach.providers import RetryableProviderError
from outreach.service import Service


CAMPAIGN = {"id": "synthetic", "name": "Synthetic test campaign", "min_score": 0}
PROFILE = {"username": "synthetic.coach", "full_name": "Mira Demo", "bio": "Krafttraining für den Alltag", "followers": 2300, "language": "de"}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="outreach-backend-", dir=os.environ.get("OUTREACH_TEST_TMP") or os.environ.get("TMPDIR"))
        self.addCleanup(self.directory.cleanup)
        self.service = Service(self.directory.name, CAMPAIGN)

    def generated(self, profile=None):
        self.service.import_profiles([profile or PROFILE])
        username = (profile or PROFILE)["username"]
        job = self.service.enqueue(username)
        self.assertTrue(self.service.run_once())
        record = self.service.profile(username)
        self.assertEqual(record["status"], "review", record)
        self.assertEqual(len(record["drafts"]), 3, record)
        return record, job

    def approved(self):
        record, job = self.generated()
        return self.service.approve(record["username"], record["drafts"][0]["id"])

    def test_agent_settings_persist_across_restart_and_override_startup_defaults(self):
        config = {**CAMPAIGN, "name": "Mira Agent", "niche": "local-services",
                  "sender": {"name": "Mira Demo", "role": "Designer", "context": "Portfolio projects"},
                  "offerings": ["Brand design"], "include_keywords": ["Studio"]}
        saved = self.service.configure_agent(config)
        persisted = Path(self.directory.name) / "agent.json"
        self.assertEqual(json.loads(persisted.read_text()), saved)
        restarted = Service(self.directory.name, {"id": "ignored-fallback", "niche": "general"})
        self.assertEqual(restarted.current_agent(), saved)
        self.assertEqual(restarted.campaign_id, CAMPAIGN["id"])
        saved["sender"]["name"] = "mutated-return-value"
        self.assertEqual(self.service.current_agent()["sender"]["name"], "Mira Demo")
        self.assertEqual(list(Path(self.directory.name).glob(".agent-*.tmp")), [])

    def test_agent_config_change_invalidates_jobs_and_approvals_preserves_contacts(self):
        rows = [{**PROFILE, "username": "synthetic." + suffix} for suffix in ("approval", "queued", "running", "sent", "suppressed")]
        self.service.import_profiles(rows)
        for suffix in ("approval", "sent"):
            username = "synthetic." + suffix
            self.service.enqueue(username)
            self.service.run_once()
            record = self.service.profile(username)
            self.service.approve(username, record["drafts"][0]["id"])
        old_approval = self.service.profile("synthetic.approval")["approved_draft"]["id"]
        self.service.mark_sent("synthetic.sent")
        self.service.suppress("synthetic.suppressed")
        self.service.enqueue("synthetic.running")
        running = self.service.store.claim_job(self.service.campaign_id)
        self.service.enqueue("synthetic.queued")
        self.service.configure_agent({**CAMPAIGN, "niche": "local-services", "include_keywords": ["Bäckerei"]})
        self.assertIsNone(self.service.profile("synthetic.approval")["approved_draft"])
        with self.assertRaises(ValueError):
            self.service.approve("synthetic.approval", old_approval)
        for suffix in ("queued", "running"):
            job = next(item for item in self.service.jobs() if item["username"] == "synthetic." + suffix)
            self.assertEqual(job["state"], "cancelled")
        self.assertFalse(self.service.store.complete_job(running, {}, []))
        self.assertEqual(self.service.profile("synthetic.sent")["status"], "sent")
        self.assertEqual(self.service.profile("synthetic.suppressed")["status"], "suppressed")
        self.assertFalse(self.service.profile("synthetic.approval")["analysis"]["eligible"])

    def test_agent_id_cannot_change_and_invalid_settings_preserve_previous(self):
        saved = self.service.configure_agent(CAMPAIGN)
        for config in ({**CAMPAIGN, "id": "different"}, {**CAMPAIGN, "min_score": -1}):
            with self.assertRaises(ValueError):
                self.service.configure_agent(config)
        self.assertEqual(self.service.current_agent(), saved)
        self.assertEqual(json.loads((Path(self.directory.name) / "agent.json").read_text()), saved)

    def test_agent_database_failure_rolls_back_settings_file(self):
        saved = self.service.configure_agent(CAMPAIGN)
        with patch.object(self.service.store, "register_campaign", side_effect=RuntimeError("private configuration payload")):
            with self.assertRaisesRegex(RuntimeError, "previous configuration was preserved") as error:
                self.service.configure_agent({**CAMPAIGN, "name": "Changed agent"})
        self.assertNotIn("private configuration", str(error.exception))
        self.assertEqual(self.service.current_agent(), saved)
        self.assertEqual(json.loads((Path(self.directory.name) / "agent.json").read_text()), saved)

    def test_agent_concurrent_configuration_keeps_file_and_database_consistent(self):
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda number: self.service.configure_agent({**CAMPAIGN, "name": "Agent " + str(number)}), range(3)))
        self.assertEqual(json.loads((Path(self.directory.name) / "agent.json").read_text()), self.service.current_agent())
        self.assertEqual(self.service.campaign, self.service.current_agent())

    def test_unreadable_saved_agent_does_not_fall_back_or_overwrite(self):
        path = Path(self.directory.name) / "agent.json"
        path.write_text("{invalid json")
        with self.assertRaisesRegex(ValueError, "not overwritten"):
            Service(self.directory.name, CAMPAIGN)
        self.assertEqual(path.read_text(), "{invalid json")

    def test_explicit_absolute_data_directory(self):
        with self.assertRaises(ValueError):
            Service("relative-path", CAMPAIGN)
        with self.assertRaises(ValueError):
            Service(None, CAMPAIGN)
        self.assertEqual(self.service.store.path.parent, Path(self.directory.name).resolve())

    def test_atomic_validation_and_case_insensitive_deduplication(self):
        with self.assertRaisesRegex(ValueError, "invalid profile rows: 2"):
            self.service.import_profiles([PROFILE, {"username": "bad name"}])
        self.assertEqual(self.service.list_profiles(), [])
        result = self.service.import_profiles([PROFILE, {**PROFILE, "username": "@SYNTHETIC.COACH"}])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(self.service.profile("@SYNTHETIC.COACH")["username"], "synthetic.coach")

    def test_enqueue_is_idempotent_before_and_after_completion(self):
        self.service.import_profiles([PROFILE])
        first = self.service.enqueue(PROFILE["username"])
        self.assertEqual(first["id"], self.service.enqueue(PROFILE["username"])["id"])
        self.service.run_once()
        self.assertEqual(first["id"], self.service.enqueue(PROFILE["username"])["id"])
        self.assertFalse(self.service.run_once())
        self.assertEqual(len(self.service.jobs()), 1)

    def test_switching_provider_back_creates_a_current_generation(self):
        record, first = self.generated()
        second = self.service.enqueue(record["username"], "openai")
        with patch("outreach.service.generate_openai_drafts", side_effect=generate_drafts):
            self.service.run_once()
        self.assertTrue(self.service.profile(record["username"])["drafts"][0]["id"].startswith(second["id"]))
        third = self.service.enqueue(record["username"], "template")
        self.assertNotEqual(third["id"], first["id"])
        self.assertEqual(third["state"], "queued")
        self.service.run_once()
        self.assertTrue(self.service.profile(record["username"])["drafts"][0]["id"].startswith(third["id"]))
        self.assertEqual(self.service.enqueue(record["username"], "template")["id"], third["id"])

    def test_only_explicit_approved_handoff_can_be_marked_sent(self):
        record, _ = self.generated()
        with self.assertRaises(ValueError):
            self.service.mark_sent(record["username"])
        with self.assertRaises(ValueError):
            self.service.mark_replied(record["username"])
        approved = self.service.approve(record["username"], record["drafts"][0]["id"])
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(len(self.service.export_rows()), 1)
        self.assertEqual(self.service.mark_sent(record["username"])["status"], "sent")
        self.assertEqual(self.service.export_rows(), [])
        self.assertEqual(self.service.mark_sent(record["username"])["status"], "sent")
        self.assertEqual(self.service.mark_replied(record["username"])["status"], "replied")
        with self.assertRaises(ValueError):
            self.service.mark_sent(record["username"])
        with self.assertRaises(ValueError):
            self.service.enqueue(record["username"])
        with self.assertRaises(ValueError):
            self.service.approve(record["username"], record["drafts"][0]["id"])
        self.assertIn("human_marked_sent", [event["event"] for event in self.service.audit()])

    def test_suppression_survives_restart_reimport_and_other_campaigns(self):
        record, _ = self.generated()
        self.service.suppress(record["username"])
        second = Service(self.directory.name, {**CAMPAIGN, "id": "another"})
        second.import_profiles([{**PROFILE, "bio": "Öffentliches Training für Berufstätige"}])
        self.assertEqual(second.profile(record["username"])["status"], "suppressed")
        self.assertEqual(Service(self.directory.name, CAMPAIGN).profile(record["username"])["status"], "suppressed")
        for action in (second.enqueue, second.mark_sent, second.mark_replied):
            with self.assertRaises(ValueError):
                action(record["username"])

    def test_import_has_analysis_before_generation(self):
        self.service.import_profiles([PROFILE])
        record = self.service.profile(PROFILE["username"])
        self.assertEqual(record["status"], "pending")
        self.assertTrue(record["analysis"]["eligible"])
        self.assertIsInstance(record["analysis"]["score"], int)
        updated = Service(self.directory.name, {**CAMPAIGN, "exclude_keywords": ["Krafttraining"]})
        self.assertFalse(self.service.profile(PROFILE["username"])["analysis"]["eligible"])
        self.assertFalse(updated.list_profiles()[0]["analysis"]["eligible"])

    def test_actual_sent_message_provenance_across_campaigns(self):
        record = self.approved()
        second = Service(self.directory.name, {**CAMPAIGN, "id": "another", "tone": "professional"})
        second.import_profiles([PROFILE])
        second.enqueue(record["username"])
        second.run_once()
        second_record = second.profile(record["username"])
        second.approve(record["username"], second_record["drafts"][1]["id"])
        self.service.mark_sent(record["username"])
        elsewhere = second.profile(record["username"])
        self.assertIsNone(elsewhere["approved_draft"])
        self.assertTrue(elsewhere["contacted_elsewhere"])
        self.assertEqual(elsewhere["contact_campaign_id"], self.service.campaign_id)
        self.assertEqual(elsewhere["contact_draft"]["text"], record["approved_draft"]["text"])
        self.assertEqual(second.stats()["campaign_sent"], 0)
        self.assertEqual(second.stats()["contacted_elsewhere"], 1)
        self.assertEqual(self.service.stats()["campaign_sent"], 1)
        self.assertEqual(second.mark_replied(record["username"])["contact_campaign_id"], self.service.campaign_id)
        self.assertEqual(second.stats()["campaign_replied"], 0)
        self.assertEqual(self.service.stats()["campaign_replied"], 1)

    def test_contacted_state_propagates_and_cancels_other_campaign_job(self):
        record = self.approved()
        second = Service(self.directory.name, {**CAMPAIGN, "id": "another"})
        second.import_profiles([PROFILE])
        second.enqueue(record["username"])
        self.service.mark_sent(record["username"])
        self.assertEqual(second.profile(record["username"])["status"], "sent")
        self.assertEqual(second.jobs()[0]["state"], "cancelled")
        second.import_profiles([{**PROFILE, "bio": "Fitnesscoaching am Morgen"}])
        self.assertEqual(second.profile(record["username"])["status"], "sent")

    def test_edit_validation_and_generation_bound_draft_ids(self):
        record, _ = self.generated()
        old_id = record["drafts"][0]["id"]
        with self.assertRaisesRegex(ValueError, "validation failed"):
            self.service.approve(record["username"], old_id, "Ich garantiere dir mehr Umsatz. Wie klingt das?")
        self.service.import_profiles([{**PROFILE, "followers": 2400}])
        with self.assertRaises(ValueError):
            self.service.approve(record["username"], old_id)
        self.service.enqueue(record["username"])
        self.service.run_once()
        self.assertNotEqual(old_id, self.service.profile(record["username"])["drafts"][0]["id"])
        with self.assertRaises(ValueError):
            self.service.approve(record["username"], old_id)

    def test_campaign_change_invalidates_approval(self):
        record = self.approved()
        updated = Service(self.directory.name, {**CAMPAIGN, "tone": "professional"})
        current = updated.profile(record["username"])
        self.assertEqual(current["status"], "pending")
        self.assertIsNone(current["approved_draft"])
        with self.assertRaises(ValueError):
            updated.mark_sent(record["username"])

    def test_multiple_threads_claim_once(self):
        self.service.import_profiles([PROFILE])
        with ThreadPoolExecutor(max_workers=6) as executor:
            jobs = list(executor.map(lambda _: self.service.enqueue(PROFILE["username"]), range(6)))
        self.assertEqual(len({job["id"] for job in jobs}), 1)
        with ThreadPoolExecutor(max_workers=6) as executor:
            claims = list(executor.map(lambda _: self.service.store.claim_job(self.service.campaign_id), range(6)))
        self.assertEqual(sum(claim is not None for claim in claims), 1)

    def test_expired_lease_reclaimed_and_old_worker_cannot_commit(self):
        clock = [1000.0]
        self.service.store.clock = lambda: clock[0]
        self.service.import_profiles([PROFILE])
        self.service.enqueue(PROFILE["username"])
        old = self.service.store.claim_job(self.service.campaign_id)
        clock[0] += 121
        new = self.service.store.claim_job(self.service.campaign_id)
        self.assertEqual(new["attempt"], 2)
        self.assertNotEqual(old["owner"], new["owner"])
        analysis = analyse_profile(new["profile"], new["campaign"])
        drafts = generate_drafts(new["profile"], analysis, new["campaign"])
        self.assertFalse(self.service.store.complete_job(old, analysis, drafts))
        self.assertTrue(self.service.store.complete_job(new, analysis, drafts))
        self.assertEqual(self.service.profile(PROFILE["username"])["status"], "review")

    def test_retryable_failure_has_backoff_and_bounded_attempts(self):
        clock = [1000.0]
        self.service.store.clock = lambda: clock[0]
        self.service.import_profiles([PROFILE])
        self.service.enqueue(PROFILE["username"], "openai")
        with patch("outreach.service.generate_openai_drafts", side_effect=RetryableProviderError("OpenAI temporarily unavailable (HTTP 429)")):
            for attempt in range(1, 4):
                self.assertTrue(self.service.run_once())
                job = self.service.jobs()[0]
                self.assertEqual(job["attempt"], attempt)
                if attempt < 3:
                    self.assertEqual(job["state"], "queued")
                    self.assertFalse(self.service.run_once())
                    clock[0] = job["not_before"]
                else:
                    self.assertEqual(job["state"], "failed")
        self.assertFalse(self.service.run_once())

    def test_expired_final_attempt_fails_instead_of_looping(self):
        clock = [1000.0]
        self.service.store.clock = lambda: clock[0]
        self.service.import_profiles([PROFILE])
        self.service.enqueue(PROFILE["username"])
        for attempt in range(1, 4):
            job = self.service.store.claim_job(self.service.campaign_id)
            self.assertEqual(job["attempt"], attempt)
            clock[0] += 121
        self.assertIsNone(self.service.store.claim_job(self.service.campaign_id))
        self.assertEqual(self.service.jobs()[0]["state"], "failed")

    def test_suppress_during_generation_discards_worker_result(self):
        self.service.import_profiles([PROFILE])
        self.service.enqueue(PROFILE["username"])
        real_generate = generate_drafts
        def generate_and_suppress(*args, **kwargs):
            self.service.suppress(PROFILE["username"])
            return real_generate(*args, **kwargs)
        with patch("outreach.service.generate_drafts", side_effect=generate_and_suppress):
            self.service.run_once()
        self.assertEqual(self.service.profile(PROFILE["username"])["status"], "suppressed")
        self.assertEqual(self.service.profile(PROFILE["username"])["drafts"], [])
        self.assertEqual(self.service.jobs()[0]["state"], "cancelled")

    def test_ineligible_profile_does_not_call_paid_provider(self):
        self.service.import_profiles([{**PROFILE, "is_private": True}])
        self.service.enqueue(PROFILE["username"], "openai")
        with patch("outreach.service.generate_openai_drafts") as provider:
            self.service.run_once()
        provider.assert_not_called()
        self.assertEqual(self.service.profile(PROFILE["username"])["drafts"], [])
        self.assertIsNotNone(self.service.profile(PROFILE["username"])["error"])

    def test_unexpected_failure_never_persists_raw_exception(self):
        self.service.import_profiles([PROFILE])
        self.service.enqueue(PROFILE["username"])
        with patch("outreach.service.generate_drafts", side_effect=RuntimeError("secret-token-fixture-do-not-log")):
            self.service.run_once()
        self.assertNotIn("secret-token", self.service.jobs()[0]["error"])
        self.assertEqual(self.service.jobs()[0]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
