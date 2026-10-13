"""Durable SQLite state, leases, and guarded human-review transitions.

A connection is opened per operation. All state changes use BEGIN IMMEDIATE;
no transaction remains open while a model or dataset API is called.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


TERMINAL = {"sent", "replied", "suppressed"}


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def unpacked(value, default=None):
    return json.loads(value) if value is not None else default


class Store:
    def __init__(self, data_dir, clock=None, lease_seconds=120):
        directory = Path(data_dir).expanduser()
        if not directory.is_absolute():
            raise ValueError("Data directory must be an explicit absolute path")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory.resolve() / "outreach.sqlite3"
        self.clock = clock or time.time
        self.lease_seconds = lease_seconds
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, config_json TEXT NOT NULL,
                    config_hash TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    raw_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    contact_state TEXT CHECK(contact_state IN ('sent','replied','suppressed')),
                    contact_campaign_id TEXT, contact_draft_json TEXT, contacted_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaign_profiles (
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                    username TEXT NOT NULL REFERENCES profiles(username),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','review','approved','sent','replied','suppressed')),
                    analysis_json TEXT, drafts_json TEXT, approved_json TEXT,
                    analysis_version INTEGER, generated_job_id TEXT, error TEXT, updated_at REAL NOT NULL,
                    PRIMARY KEY(campaign_id, username)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                    username TEXT NOT NULL REFERENCES profiles(username), provider TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed','cancelled')),
                    attempt INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                    not_before REAL NOT NULL, lease_until REAL, owner TEXT,
                    profile_version INTEGER NOT NULL, campaign_version INTEGER NOT NULL,
                    error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job
                    ON jobs(campaign_id, username) WHERE state IN ('queued','running');
                CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state, not_before, created_at);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT,
                    username TEXT, event TEXT NOT NULL, details_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
            # Forward-only, additive migration for data created by the first MVP.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(profiles)")}
            for name, kind in (("contact_campaign_id", "TEXT"), ("contact_draft_json", "TEXT"), ("contacted_at", "REAL")):
                if name not in columns:
                    db.execute("ALTER TABLE profiles ADD COLUMN " + name + " " + kind)
            campaign_columns = {row["name"] for row in db.execute("PRAGMA table_info(campaign_profiles)")}
            if "generated_job_id" not in campaign_columns:
                db.execute("ALTER TABLE campaign_profiles ADD COLUMN generated_job_id TEXT")
            db.execute("PRAGMA user_version=3")

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA journal_mode=DELETE")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, campaign, username, event, details=None):
        db.execute("INSERT INTO audit_events(campaign_id,username,event,details_json,created_at) VALUES(?,?,?,?,?)",
                   (campaign, username, event, packed(details or {}), self.clock()))

    def register_campaign(self, config):
        blob = packed(config)
        digest = hashlib.sha256(blob.encode()).hexdigest()
        now = self.clock()
        with self.connection(True) as db:
            old = db.execute("SELECT * FROM campaigns WHERE id=?", (config["id"],)).fetchone()
            if old is None:
                db.execute("INSERT INTO campaigns(id,config_json,config_hash,created_at,updated_at) VALUES(?,?,?,?,?)",
                           (config["id"], blob, digest, now, now))
                self.event(db, config["id"], None, "campaign_created")
            elif old["config_hash"] != digest:
                db.execute("UPDATE campaigns SET config_json=?,config_hash=?,version=version+1,updated_at=? WHERE id=?",
                           (blob, digest, now, config["id"]))
                db.execute("""UPDATE campaign_profiles SET status='pending',analysis_json=NULL,drafts_json=NULL,
                           approved_json=NULL,analysis_version=NULL,generated_job_id=NULL,error=NULL,updated_at=?
                           WHERE campaign_id=? AND status NOT IN ('sent','replied','suppressed')""", (now, config["id"]))
                db.execute("""UPDATE jobs SET state='cancelled',error='Campaign changed; enqueue a new job',
                           owner=NULL,lease_until=NULL,updated_at=? WHERE campaign_id=? AND state IN ('queued','running')""",
                           (now, config["id"]))
                self.event(db, config["id"], None, "campaign_changed", {"version": old["version"] + 1})

    def campaign_config(self, campaign):
        with self.connection() as db:
            row = db.execute("SELECT config_json FROM campaigns WHERE id=?", (campaign,)).fetchone()
            if row is None:
                raise ValueError("Campaign not found")
            return unpacked(row[0])

    def import_profiles(self, campaign, profiles):
        summary = {"imported": 0, "updated": 0, "duplicates": 0, "errors": []}
        seen = set()
        now = self.clock()
        with self.connection(True) as db:
            for profile in profiles:
                username = profile["username"]
                if username in seen:
                    summary["duplicates"] += 1
                    continue
                seen.add(username)
                blob = packed(profile)
                old = db.execute("SELECT * FROM profiles WHERE username=?", (username,)).fetchone()
                if old is None:
                    db.execute("INSERT INTO profiles(username,raw_json,created_at,updated_at) VALUES(?,?,?,?)",
                               (username, blob, now, now))
                    state = "pending"
                    summary["imported"] += 1
                else:
                    state = old["contact_state"] or "pending"
                    if old["raw_json"] != blob:
                        db.execute("UPDATE profiles SET raw_json=?,version=version+1,updated_at=? WHERE username=?",
                                   (blob, now, username))
                        db.execute("""UPDATE campaign_profiles SET status='pending',analysis_json=NULL,drafts_json=NULL,
                                   approved_json=NULL,analysis_version=NULL,generated_job_id=NULL,error=NULL,updated_at=?
                                   WHERE username=? AND status NOT IN ('sent','replied','suppressed')""", (now, username))
                        db.execute("""UPDATE jobs SET state='cancelled',error='Profile changed; enqueue a new job',
                                   owner=NULL,lease_until=NULL,updated_at=? WHERE username=? AND state IN ('queued','running')""",
                                   (now, username))
                        summary["updated"] += 1
                    else:
                        summary["duplicates"] += 1
                db.execute("""INSERT OR IGNORE INTO campaign_profiles(campaign_id,username,status,updated_at)
                           VALUES(?,?,?,?)""", (campaign, username, state, now))
                self.event(db, campaign, username, "profile_imported", {"contact_state": state})
        return summary

    @staticmethod
    def _profile(db, campaign, username):
        row = db.execute("""SELECT cp.*,p.raw_json,p.version,p.contact_state,p.contact_campaign_id,p.contact_draft_json,p.contacted_at FROM campaign_profiles cp
                          JOIN profiles p USING(username) WHERE cp.campaign_id=? AND cp.username=?""",
                         (campaign, username)).fetchone()
        if row is None:
            raise ValueError("Profile not found in this campaign")
        return row

    @staticmethod
    def _public_profile(row):
        return {"username": row["username"], "status": row["contact_state"] or row["status"],
                "profile": unpacked(row["raw_json"]), "analysis": unpacked(row["analysis_json"]),
                "drafts": unpacked(row["drafts_json"], []), "approved_draft": unpacked(row["approved_json"]),
                "error": row["error"], "updated_at": row["updated_at"], "version": row["version"],
                "contact_campaign_id": row["contact_campaign_id"],
                "contacted_elsewhere": bool(row["contact_campaign_id"] and row["contact_campaign_id"] != row["campaign_id"]),
                "contact_draft": unpacked(row["contact_draft_json"]), "contacted_at": row["contacted_at"]}

    def profile(self, campaign, username):
        with self.connection() as db:
            return self._public_profile(self._profile(db, campaign, username))

    def list_profiles(self, campaign):
        with self.connection() as db:
            rows = db.execute("""SELECT cp.*,p.raw_json,p.version,p.contact_state,p.contact_campaign_id,p.contact_draft_json,p.contacted_at FROM campaign_profiles cp
                              JOIN profiles p USING(username) WHERE cp.campaign_id=? ORDER BY cp.updated_at DESC,cp.username""",
                              (campaign,)).fetchall()
            return [self._public_profile(row) for row in rows]

    def enqueue(self, campaign, username, provider):
        now = self.clock()
        with self.connection(True) as db:
            profile = self._profile(db, campaign, username)
            if profile["contact_state"] in TERMINAL or profile["status"] in TERMINAL:
                raise ValueError("Contacted or suppressed profiles cannot be generated again")
            active = db.execute("SELECT * FROM jobs WHERE campaign_id=? AND username=? AND state IN ('queued','running')",
                                (campaign, username)).fetchone()
            if active:
                if active["provider"] != provider:
                    raise ValueError("A job for another provider is already active")
                return dict(active)
            version = db.execute("SELECT version FROM campaigns WHERE id=?", (campaign,)).fetchone()[0]
            done = db.execute("""SELECT * FROM jobs WHERE campaign_id=? AND username=? AND provider=?
                              AND profile_version=? AND campaign_version=? AND state='completed'
                              ORDER BY created_at DESC LIMIT 1""",
                              (campaign, username, provider, profile["version"], version)).fetchone()
            if done and profile["generated_job_id"] == done["id"] and profile["status"] in {"review", "approved"}:
                return dict(done)
            if profile["status"] == "approved":
                raise ValueError("An approved draft cannot be regenerated; retain its existing approval")
            job_id = uuid.uuid4().hex
            db.execute("""INSERT INTO jobs(id,campaign_id,username,provider,state,not_before,profile_version,
                       campaign_version,created_at,updated_at) VALUES(?,?,?,?,'queued',?,?,?,?,?)""",
                       (job_id, campaign, username, provider, now, profile["version"], version, now, now))
            db.execute("UPDATE campaign_profiles SET error=NULL,updated_at=? WHERE campaign_id=? AND username=?",
                       (now, campaign, username))
            self.event(db, campaign, username, "job_enqueued", {"job_id": job_id, "provider": provider})
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def claim_job(self, campaign):
        now = self.clock()
        with self.connection(True) as db:
            expired = db.execute("SELECT * FROM jobs WHERE campaign_id=? AND state='running' AND lease_until<=?",
                                 (campaign, now)).fetchall()
            for job in expired:
                state = "failed" if job["attempt"] >= job["max_attempts"] else "queued"
                error = "Worker lease expired; retry limit reached" if state == "failed" else "Worker lease expired; retry scheduled"
                db.execute("UPDATE jobs SET state=?,owner=NULL,lease_until=NULL,error=?,not_before=?,updated_at=? WHERE id=?",
                           (state, error, now, now, job["id"]))
                if state == "failed":
                    db.execute("UPDATE campaign_profiles SET error=?,updated_at=? WHERE campaign_id=? AND username=?",
                               (error, now, campaign, job["username"]))
                self.event(db, campaign, job["username"], "job_lease_expired", {"job_id": job["id"], "state": state})
            job = db.execute("""SELECT * FROM jobs WHERE campaign_id=? AND state='queued' AND not_before<=?
                              ORDER BY created_at,id LIMIT 1""", (campaign, now)).fetchone()
            if job is None:
                return None
            profile = self._profile(db, campaign, job["username"])
            campaign_row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign,)).fetchone()
            if profile["contact_state"] or profile["version"] != job["profile_version"] or campaign_row["version"] != job["campaign_version"]:
                db.execute("UPDATE jobs SET state='cancelled',error='Profile or campaign is no longer eligible',updated_at=? WHERE id=?",
                           (now, job["id"]))
                self.event(db, campaign, job["username"], "job_cancelled", {"job_id": job["id"]})
                return {"cancelled": True}
            owner = uuid.uuid4().hex
            db.execute("UPDATE jobs SET state='running',attempt=attempt+1,lease_until=?,owner=?,error=NULL,updated_at=? WHERE id=?",
                       (now + self.lease_seconds, owner, now, job["id"]))
            claimed = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
            claimed["profile"] = unpacked(profile["raw_json"])
            claimed["campaign"] = unpacked(campaign_row["config_json"])
            self.event(db, campaign, job["username"], "job_claimed", {"job_id": job["id"], "attempt": claimed["attempt"]})
            return claimed

    def _live_claim(self, db, job):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        return row and row["state"] == "running" and row["owner"] == job["owner"] and row["lease_until"] > self.clock()

    def complete_job(self, job, analysis, drafts):
        now = self.clock()
        with self.connection(True) as db:
            if not self._live_claim(db, job):
                return False
            profile = self._profile(db, job["campaign_id"], job["username"])
            version = db.execute("SELECT version FROM campaigns WHERE id=?", (job["campaign_id"],)).fetchone()[0]
            if profile["contact_state"] or profile["version"] != job["profile_version"] or version != job["campaign_version"]:
                db.execute("UPDATE jobs SET state='cancelled',owner=NULL,lease_until=NULL,error='Stale result discarded',updated_at=? WHERE id=?",
                           (now, job["id"]))
                self.event(db, job["campaign_id"], job["username"], "stale_result_discarded", {"job_id": job["id"]})
                return False
            error = None if drafts else "No eligible evidence-backed draft; review the profile and campaign criteria"
            db.execute("""UPDATE campaign_profiles SET status='review',analysis_json=?,drafts_json=?,approved_json=NULL,
                       analysis_version=?,generated_job_id=?,error=?,updated_at=? WHERE campaign_id=? AND username=?""",
                       (packed(analysis), packed(drafts), job["profile_version"], job["id"], error, now, job["campaign_id"], job["username"]))
            db.execute("UPDATE jobs SET state='completed',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?", (now, job["id"]))
            self.event(db, job["campaign_id"], job["username"], "drafts_generated", {"job_id": job["id"], "count": len(drafts)})
            return True

    def fail_job(self, job, error, retryable=False):
        now = self.clock()
        with self.connection(True) as db:
            if not self._live_claim(db, job):
                return False
            retry = retryable and job["attempt"] < job["max_attempts"]
            state = "queued" if retry else "failed"
            delay = min(300, 2 ** job["attempt"] * 5) if retry else 0
            db.execute("UPDATE jobs SET state=?,error=?,owner=NULL,lease_until=NULL,not_before=?,updated_at=? WHERE id=?",
                       (state, error, now + delay, now, job["id"]))
            db.execute("UPDATE campaign_profiles SET error=?,updated_at=? WHERE campaign_id=? AND username=?",
                       (error, now, job["campaign_id"], job["username"]))
            self.event(db, job["campaign_id"], job["username"], "job_retry_scheduled" if retry else "job_failed",
                       {"job_id": job["id"], "attempt": job["attempt"], "error": error})
            return True

    def approve(self, campaign, username, draft_id, text, validator):
        now = self.clock()
        with self.connection(True) as db:
            profile = self._profile(db, campaign, username)
            if profile["contact_state"] or profile["status"] not in {"review", "approved"}:
                raise ValueError("Only a current draft in review can be approved")
            if profile["analysis_version"] != profile["version"]:
                raise ValueError("Profile changed; regenerate before approving")
            active = db.execute("SELECT 1 FROM jobs WHERE campaign_id=? AND username=? AND state IN ('queued','running')", (campaign, username)).fetchone()
            if active:
                raise ValueError("Wait for the active generation job before approving")
            drafts = unpacked(profile["drafts_json"], [])
            draft = next((dict(item) for item in drafts if item["id"] == draft_id), None)
            if draft is None:
                raise ValueError("Draft does not belong to this profile's current generation")
            if text is not None:
                if not isinstance(text, str):
                    raise ValueError("Draft text must be a string")
                draft["text"] = text.strip()
            config = unpacked(db.execute("SELECT config_json FROM campaigns WHERE id=?", (campaign,)).fetchone()[0])
            violations = validator(draft["text"], unpacked(profile["analysis_json"]), config, draft.get("evidence_ids", []))
            if violations:
                raise ValueError("Draft validation failed: " + "; ".join(str(item) for item in violations))
            previous = unpacked(profile["approved_json"])
            if profile["status"] == "approved" and previous and previous.get("id") == draft_id and previous.get("text") == draft["text"]:
                return self._public_profile(profile)
            draft["approved_at"] = now
            draft["edited"] = text is not None
            db.execute("UPDATE campaign_profiles SET status='approved',approved_json=?,error=NULL,updated_at=? WHERE campaign_id=? AND username=?",
                       (packed(draft), now, campaign, username))
            self.event(db, campaign, username, "draft_approved", {"draft_id": draft_id, "edited": text is not None})
            return self._public_profile(self._profile(db, campaign, username))

    def transition(self, campaign, username, target):
        if target not in TERMINAL:
            raise ValueError("Unsupported contact transition")
        now = self.clock()
        with self.connection(True) as db:
            profile = self._profile(db, campaign, username)
            current = profile["contact_state"] or profile["status"]
            if current == target:
                return self._public_profile(profile)
            if current == "suppressed":
                raise ValueError("Suppression is permanent in this MVP")
            if target == "sent" and (current != "approved" or not profile["approved_json"]):
                raise ValueError("Only an approved draft can be manually marked as sent")
            if target == "replied" and current != "sent":
                raise ValueError("Only a sent profile can be marked as replied")
            if target == "sent":
                db.execute("UPDATE profiles SET contact_state=?,contact_campaign_id=?,contact_draft_json=?,contacted_at=?,updated_at=? WHERE username=?",
                           (target, campaign, profile["approved_json"], now, now, username))
                # Another campaign's prepared message was never sent. Keep the
                # actual handoff provenance globally and remove stale approvals.
                db.execute("UPDATE campaign_profiles SET approved_json=NULL WHERE username=? AND campaign_id<>?", (username, campaign))
            else:
                db.execute("UPDATE profiles SET contact_state=?,updated_at=? WHERE username=?", (target, now, username))
            db.execute("UPDATE campaign_profiles SET status=?,error=NULL,updated_at=? WHERE username=?", (target, now, username))
            db.execute("UPDATE jobs SET state='cancelled',owner=NULL,lease_until=NULL,error='Contact state prevents generation',updated_at=? WHERE username=? AND state IN ('queued','running')",
                       (now, username))
            self.event(db, campaign, username, "human_marked_" + target)
            return self._public_profile(self._profile(db, campaign, username))

    def jobs(self, campaign):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs WHERE campaign_id=? ORDER BY created_at DESC,id", (campaign,))]

    def audit(self, campaign):
        with self.connection() as db:
            return [{**dict(row), "details": unpacked(row["details_json"])} for row in db.execute(
                "SELECT * FROM audit_events WHERE campaign_id=? ORDER BY id", (campaign,))]
