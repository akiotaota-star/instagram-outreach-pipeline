"""Application service shared by CLI, HTTP UI, and the local job worker."""
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from .engine import analyse_profile, generate_drafts, normalize_profile, validate_campaign, validate_draft
from .providers import PermanentProviderError, RetryableProviderError, generate_openai_drafts
from .store import Store


def username_key(value):
    if not isinstance(value, str):
        raise ValueError("username must be a string")
    return normalize_profile({"username": value})["username"]


class Service:
    def __init__(self, data_dir, campaign):
        if not data_dir:
            raise ValueError("Set OUTREACH_DATA_DIR to an explicit absolute directory")
        directory = Path(data_dir).expanduser()
        if not directory.is_absolute():
            raise ValueError("Data directory must be an explicit absolute path")
        self._settings_lock = RLock()
        self.agent_path = directory.resolve() / "agent.json"
        if self.agent_path.is_symlink():
            raise ValueError("agent.json must be a regular file inside the data directory")
        if self.agent_path.exists():
            try:
                with self.agent_path.open("r", encoding="utf-8") as handle:
                    campaign = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Saved agent.json is unreadable or invalid; existing settings were not overwritten") from None
        self.campaign = validate_campaign(campaign)
        self.campaign_id = self.campaign["id"]
        self.store = Store(data_dir)
        self.store.register_campaign(self.campaign)

    @staticmethod
    def _replace_settings(path, contents):
        """Atomic replacement using a unique temporary file beside agent.json."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".agent-", suffix=".tmp", dir=str(path.parent), delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(path))
            temporary = None
            # Best-effort directory sync (some removable filesystems reject it).
            try:
                descriptor = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def current_agent(self):
        """Return a detached copy of the current persisted campaign configuration."""
        with self._settings_lock:
            return self.store.campaign_config(self.campaign_id)

    def configure_agent(self, config):
        """Save this installation's identity, audience, offering, and writing rules.

        The campaign id is immutable. Use another data directory for a separate
        person's installation. File intent is persisted before the database is
        updated; a restart reconciles an interruption between those two steps.
        """
        validated = validate_campaign(config)
        with self._settings_lock:
            if validated["id"] != self.campaign_id:
                raise ValueError("Agent id cannot be changed; use a separate data directory for another agent")
            if self.agent_path.is_symlink():
                raise ValueError("agent.json must be a regular file inside the data directory")
            previous = self.agent_path.read_bytes() if self.agent_path.exists() else None
            contents = (json.dumps(validated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            self._replace_settings(self.agent_path, contents)
            try:
                self.store.register_campaign(validated)
            except Exception:
                try:
                    if previous is None:
                        self.agent_path.unlink(missing_ok=True)
                    else:
                        self._replace_settings(self.agent_path, previous)
                except OSError:
                    raise RuntimeError("Agent settings could not be applied; restart to reconcile the saved settings") from None
                raise RuntimeError("Agent settings could not be applied; previous configuration was preserved") from None
            self.campaign = validated
            self.campaign_id = validated["id"]
            return json.loads(json.dumps(validated, ensure_ascii=False))

    def import_profiles(self, rows):
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError("Import must be a list of at most 10000 profile objects")
        normalized = []
        errors = []
        # Validate the entire batch before committing even a single profile.
        for index, row in enumerate(rows, 1):
            try:
                normalized.append(normalize_profile(row))
            except (ValueError, TypeError, OverflowError):
                errors.append(index)
        if errors:
            raise ValueError("Import rejected atomically; invalid profile rows: " + ", ".join(map(str, errors[:50]))
                             + (" (and more)" if len(errors) > 50 else ""))
        return self.store.import_profiles(self.campaign_id, normalized)

    def enqueue(self, username, provider="template"):
        if provider not in {"template", "openai"}:
            raise ValueError("provider must be template or openai")
        return self.store.enqueue(self.campaign_id, username_key(username), provider)

    def run_once(self):
        job = self.store.claim_job(self.campaign_id)
        if job is None:
            return False
        if job.get("cancelled"):
            return True
        try:
            profile, campaign = job["profile"], job["campaign"]
            analysis = analyse_profile(profile, campaign)
            if not analysis.get("eligible"):
                drafts = []
            elif job["provider"] == "openai":
                drafts = generate_openai_drafts(profile, analysis, campaign)
            else:
                drafts = generate_drafts(profile, analysis, campaign, provider="template")
            if not isinstance(drafts, list) or len(drafts) > 3:
                raise PermanentProviderError("Generation returned an invalid number of drafts")
            safe_drafts = []
            for index, draft in enumerate(drafts):
                if not isinstance(draft, dict) or not isinstance(draft.get("text"), str):
                    raise PermanentProviderError("Generation returned invalid draft data")
                if validate_draft(draft["text"], analysis, campaign, draft.get("evidence_ids", [])):
                    raise PermanentProviderError("Generated draft failed local evidence or message validation")
                # IDs identify this generation, not just the resulting text. Old UI
                # approvals can never accidentally approve a replacement candidate.
                safe_drafts.append({**draft, "id": job["id"] + "-" + str(index + 1)})
            self.store.complete_job(job, analysis, safe_drafts)
        except RetryableProviderError as exc:
            self.store.fail_job(job, str(exc), retryable=True)
        except PermanentProviderError as exc:
            self.store.fail_job(job, str(exc), retryable=False)
        except Exception:
            # No response body, request, exception traceback or credential enters
            # the database/API. Explicit errors above retain safe diagnostic text.
            self.store.fail_job(job, "Generation failed; check the campaign and provider configuration", retryable=False)
        return True

    def _with_analysis(self, profile, campaign):
        if profile["analysis"] is None:
            profile["analysis"] = analyse_profile(profile["profile"], campaign)
        return profile

    def list_profiles(self):
        campaign = self.store.campaign_config(self.campaign_id)
        return [self._with_analysis(profile, campaign) for profile in self.store.list_profiles(self.campaign_id)]

    def profile(self, username):
        campaign = self.store.campaign_config(self.campaign_id)
        return self._with_analysis(self.store.profile(self.campaign_id, username_key(username)), campaign)

    def approve(self, username, draft_id, text=None):
        if not isinstance(draft_id, str) or not draft_id:
            raise ValueError("draft_id is required")
        return self.store.approve(self.campaign_id, username_key(username), draft_id, text, validate_draft)

    def mark_sent(self, username):
        """Record a human's explicit confirmation. Does not send a message."""
        return self.store.transition(self.campaign_id, username_key(username), "sent")

    def mark_replied(self, username):
        return self.store.transition(self.campaign_id, username_key(username), "replied")

    def suppress(self, username):
        return self.store.transition(self.campaign_id, username_key(username), "suppressed")

    def jobs(self):
        return self.store.jobs(self.campaign_id)

    def audit(self):
        return self.store.audit(self.campaign_id)

    def stats(self):
        profiles = self.list_profiles()
        statuses = Counter(profile["status"] for profile in profiles)
        jobs = Counter(job["state"] for job in self.jobs())
        return {"total": len(profiles), "profiles": len(profiles),
                "campaign_sent": sum(p["status"] in {"sent", "replied"} and p["contact_campaign_id"] == self.campaign_id for p in profiles),
                "campaign_replied": sum(p["status"] == "replied" and p["contact_campaign_id"] == self.campaign_id for p in profiles),
                "contacted_elsewhere": sum(p["contacted_elsewhere"] for p in profiles),
                "statuses": {key: statuses.get(key, 0) for key in ("pending", "review", "approved", "sent", "replied", "suppressed")},
                "jobs": {key: jobs.get(key, 0) for key in ("queued", "running", "completed", "failed", "cancelled")}}

    def export_rows(self):
        rows = []
        for profile in self.list_profiles():
            draft = profile["approved_draft"]
            if profile["status"] == "approved" and draft:
                rows.append({"username": profile["username"], "profile_url": profile["profile"]["profile_url"],
                             "message": draft["text"], "draft_id": draft["id"],
                             "evidence_ids": ",".join(draft.get("evidence_ids", [])), "status": "approved"})
        return rows
