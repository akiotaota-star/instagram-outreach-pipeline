"""Deterministic, evidence-bound qualification and opener templates.

Profile text is untrusted data. This module never executes it, fetches a URL,
contacts a model, or sends a message. Scores are sorting heuristics, not predictions.
"""
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit

LANGUAGES = {"de", "en"}
DEFAULT_QUESTIONS = {
    "de": ["Was macht dir daran gerade am meisten Spaß?",
           "Wie bist du zu diesem Thema gekommen?",
           "Welcher Aspekt davon beschäftigt dich gerade am meisten?"],
    "en": ["What do you enjoy most about that at the moment?",
           "How did you get into that?",
           "Which part of that interests you most right now?"],
}
INSTRUCTION_PATTERN = re.compile(
    r"ignore\s+(all\s+|any\s+|previous\s+|the\s+)*(instructions?|rules?|prompts?)|"
    r"ignoriere\s+.{0,35}(anweisung|regel)|system\s*prompt|developer\s*message|"
    r"you are (chatgpt|an? assistant)|assistant\s*:|<\s*(script|iframe)|"
    r"api[_ -]?key|access[_ -]?token|drop\s+table|exfiltrat", re.I)
FORBIDDEN_PATTERN = re.compile(
    r"\b(garantier\w*|guarantee\w*|umsatz\w*|revenue|profit|income|einkommen|"
    r"calendly|whatsapp|zoom|call|book a call|schedule a call|termin buchen|"
    r"telefonieren|telefonnummer|kaufinteresse|buy your|purchase your)\b|"
    r"\b(i watched|i loved|i love your|i've watched|ich habe.{0,25}(angeschaut|gesehen)|"
    r"ich liebe dein|du verlierst|you're losing|you are losing|you need|du brauchst|"
    r"i can help|ich kann dir helfen|mehr kunden|more clients|your struggle|"
    r"deine probleme|dein problem|work with you|mit dir arbeiten|"
    r"interested in your coaching|looking for a coach|suche einen coach|"
    r"lass uns.{0,15}sprechen|let.s.{0,15}chat|beeindruckend|impressive)\b", re.I)
LOW_INFORMATION = {
    "welcome to my page", "welcome to my profile", "just living life",
    "living my best life", "be yourself", "link in bio", "official account",
    "personal blog", "have a nice day", "life is beautiful",
    "willkommen auf meinem profil", "lebe dein leben",
}
OPEN_QUESTION = re.compile(r"^(wie|was|welch\w*|woran|wofür|worauf|how|what|which)\b", re.I)


def _text(value, limit=4000):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Text fields must be strings")
    text = unicodedata.normalize("NFC", value)
    text = "".join(c for c in text if not unicodedata.category(c).startswith("C") or c in "\n\t")
    return " ".join(text.split())[:limit]


def _url(value):
    if not value:
        return ""
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        raise ValueError("URLs must be HTTP(S) strings without whitespace")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("URLs must be HTTP(S) without credentials")
        _ = parts.port
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid HTTP(S) URL") from exc
    if len(value) > 2048:
        raise ValueError("URL exceeds 2048 characters")
    return value


def _boolean(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if value in (0, 1) and isinstance(value, int):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError("is_private must be a boolean")


def _integer(value, field, minimum=0, maximum=10_000_000_000, optional=False):
    if optional and (value is None or isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{1,3}(,\d{3})+|\d+", value):
        value = int(value.replace(",", ""))
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("%s must be an integer between %s and %s" % (field, minimum, maximum))
    return value


def _first(raw, *keys, default=None):
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return default


def normalize_profile(raw):
    """Normalize canonical/Apify fields. Unknown followers remain None.

    A public profile can be normalized with sparse data, but cannot receive a draft
    without usable public evidence. URLs are metadata, never fetched here.
    """
    if not isinstance(raw, dict):
        raise ValueError("Each profile must be an object")
    username = _first(raw, "username", "handle", default="")
    if not isinstance(username, str):
        raise ValueError("username must be a string")
    username = username.strip().removeprefix("@").lower()
    if (not re.fullmatch(r"[a-z0-9_][a-z0-9._]{0,29}", username)
            or username.endswith(".") or ".." in username):
        raise ValueError("Invalid Instagram username")
    posts = _first(raw, "posts", "latestPosts", default=[])
    if not isinstance(posts, list) or len(posts) > 1000:
        raise ValueError("posts must be an array with at most 1000 items")
    normalized_posts = []
    for post in posts:
        if not isinstance(post, dict):
            raise ValueError("Every post must be an object")
        text = _text(_first(post, "text", "caption", default=""))
        url = _url(_first(post, "url", "post_url", default=""))
        if text:
            normalized_posts.append({"text": text, "url": url})
    language = _text(raw.get("language"), 10).lower()
    if language and not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", language):
        raise ValueError("language must be a two-letter language code")
    source = raw.get("_source", {})
    if not isinstance(source, dict):
        raise ValueError("_source must be an object")
    metadata = {key: _text(source[key], 200) for key in ("provider", "dataset_id") if key in source}
    if "item_offset" in source:
        metadata["item_offset"] = _integer(source["item_offset"], "item_offset")
    profile_url = "https://www.instagram.com/%s/" % username
    return {
        "username": username,
        "_source": metadata,
        "full_name": _text(_first(raw, "full_name", "fullName", default=""), 150),
        "bio": _text(_first(raw, "bio", "biography", default="")),
        "followers": _integer(_first(raw, "followers", "followersCount"), "followers", optional=True),
        "is_private": _boolean(_first(raw, "is_private", "isPrivate", "private")),
        "website": _url(_first(raw, "website", "externalUrl", "external_url", default="")),
        "posts": normalized_posts[:50],
        "profile_url": profile_url,
        "language": language.split("-")[0] if language else None,
        "source_url": _url(_first(raw, "source_url", "profile_url", "url", default=profile_url)),
    }


def _strings(value, field, limit=100):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("%s must be an array of at most %s strings" % (field, limit))
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 200:
            raise ValueError("%s must contain nonempty strings of at most 200 characters" % field)
        result.append(_text(item, 200))
    return list(dict.fromkeys(result))


def validate_campaign(data):
    """Return a fresh campaign with defaults, or raise ValueError.

    Hook rules are ordered. A rule is chosen only when one of its literal
    keywords appears in the chosen evidence, rather than elsewhere in a profile.
    """
    if not isinstance(data, dict):
        raise ValueError("Campaign must be an object")
    campaign = {
        "id": _text(data.get("id", "default"), 80),
        "name": _text(data.get("name", "Evidence-based outreach"), 120),
        "niche": _text(data.get("niche", "general"), 80),
        "language": data.get("language", "de"),
        "languages": data.get("languages", []),
        "include_keywords": _strings(data.get("include_keywords", []), "include_keywords"),
        "exclude_keywords": _strings(data.get("exclude_keywords", []), "exclude_keywords"),
        "min_followers": _integer(data.get("min_followers", 0), "min_followers"),
        "max_followers": _integer(data.get("max_followers", 10_000_000_000), "max_followers"),
        "min_score": _integer(data.get("min_score", 35), "min_score", maximum=100),
        "max_chars": _integer(data.get("max_chars", 420), "max_chars", minimum=180, maximum=1000),
        "max_words": _integer(data.get("max_words", 75), "max_words", minimum=30, maximum=150),
        "tone": data.get("tone", "friendly"),
        "offerings": _strings(data.get("offerings", []), "offerings", 20),
        "sender": data.get("sender", {}),
        "hook_rules": [],
    }
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", campaign["id"]):
        raise ValueError("Campaign id must contain only letters, digits, underscores or hyphens")
    if not isinstance(campaign["language"], str) or campaign["language"] not in LANGUAGES:
        raise ValueError("Supported template languages: de, en")
    campaign["languages"] = _strings(campaign["languages"], "languages", 2)
    if any(lang not in LANGUAGES for lang in campaign["languages"]):
        raise ValueError("Supported filter languages: de, en")
    if not isinstance(campaign["tone"], str) or campaign["tone"] not in {"friendly", "casual", "professional"}:
        raise ValueError("tone must be friendly, casual or professional")
    if campaign["min_followers"] > campaign["max_followers"]:
        raise ValueError("min_followers must not exceed max_followers")
    if not isinstance(campaign["sender"], dict):
        raise ValueError("sender must be an object")
    campaign["sender"] = {key: _text(campaign["sender"].get(key, ""), 200)
                          for key in ("name", "role", "context")}
    rules = data.get("hook_rules", [])
    if not isinstance(rules, list) or len(rules) > 30:
        raise ValueError("hook_rules must be an array of at most 30 rules")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Each hook rule must be an object")
        keywords = _strings(rule.get("keywords", []), "hook keywords", 30)
        questions = rule.get("questions", {})
        if not keywords or not isinstance(questions, dict) or not questions:
            raise ValueError("Each hook rule requires keywords and questions")
        clean_questions = {}
        for language, values in questions.items():
            if language not in LANGUAGES or not isinstance(values, list) or len(values) != 3:
                raise ValueError("Each hook language must contain exactly three questions")
            values = _strings(values, "hook questions", 3)
            if len(values) != 3:
                raise ValueError("Hook questions must be distinct")
            for question in values:
                if (len(question) > 120 or question.count("?") != 1 or not question.endswith("?")
                        or not OPEN_QUESTION.match(question) or INSTRUCTION_PATTERN.search(question)
                        or FORBIDDEN_PATTERN.search(question) or "://" in question
                        or any(mark in question for mark in ('“', '”', '„', '"'))):
                    raise ValueError("Hook questions must be short open questions without pitches or unsupported claims")
            clean_questions[language] = values
        campaign["hook_rules"].append({"keywords": keywords, "questions": clean_questions})
    return campaign


def _keyword_in(keyword, text):
    return re.search(r"(?<!\w)" + re.escape(keyword.casefold()) + r"(?!\w)", text.casefold()) is not None


def _excerpt(text, limit=100):
    # A quote is an exact substring of normalized source text, never a paraphrase.
    parts = re.split(r"\s*[|•]\s*|(?<=[.!?])\s+", text)
    for part in parts:
        part = part.strip(" \"“”„'")
        if len(part) > limit:
            part = part[:limit].rsplit(" ", 1)[0].rstrip(",;:")
        words = re.findall(r"[^\W\d_]+", part, re.UNICODE)
        if (len(part) >= 10 and len(words) >= 2 and part.casefold().rstrip(".!") not in LOW_INFORMATION
                and not INSTRUCTION_PATTERN.search(part)
                and not FORBIDDEN_PATTERN.search(part) and not re.search(r"https?://|www\.", part)
                and not any(mark in part for mark in ('?', '“', '”', '„', '"', '<', '>'))):
            return part
    return ""


def _evidence(profile):
    result = []
    sources = [("bio", profile["bio"], profile["profile_url"])]
    sources += [("post", item["text"], item["url"] or profile["profile_url"]) for item in profile["posts"]]
    for kind, text, url in sources:
        if not text:
            continue
        quote = _excerpt(text)
        # Detect instructions across the full source, even if a safe prefix exists.
        usable = bool(quote) and not INSTRUCTION_PATTERN.search(text)
        evidence_text = quote if usable else text[:100]
        evidence_id = "ev_" + hashlib.sha256((kind + "\0" + url + "\0" + evidence_text).encode()).hexdigest()[:16]
        if not any(item["id"] == evidence_id for item in result):
            result.append({"id": evidence_id, "text": evidence_text, "url": url,
                           "kind": kind, "usable": bool(usable)})
    return result


def analyse_profile(profile, campaign):
    """Qualify public data. Keyword fit is a heuristic, never inferred need."""
    profile = normalize_profile(profile)
    campaign = validate_campaign(campaign)
    blocked = []
    reasons = []
    evidence = _evidence(profile) if not profile["is_private"] else []
    usable = [item for item in evidence if item["usable"]]
    searchable = " ".join([profile["bio"]] + [p["text"] for p in profile["posts"]])
    matched = [key for key in campaign["include_keywords"] if _keyword_in(key, searchable)]
    excluded = [key for key in campaign["exclude_keywords"] if _keyword_in(key, searchable)]
    score = 10 if not profile["is_private"] else 0
    if profile["is_private"]:
        blocked.append("private_profile")
    if not usable:
        blocked.append("no_usable_public_evidence")
    if campaign["include_keywords"] and not matched:
        blocked.append("no_target_keyword_match")
    if excluded:
        blocked.append("excluded_keyword_match")
        reasons.append("Excluded keyword(s): " + ", ".join(excluded))
    if matched:
        score += min(30, len(matched) * 15)
        reasons.append("Target keyword(s): " + ", ".join(matched))
    elif not campaign["include_keywords"]:
        score += 15
        reasons.append("No niche keyword filter configured")
    if any(e["kind"] == "bio" for e in usable):
        score += 20
        reasons.append("Usable public bio excerpt")
    if any(e["kind"] == "post" for e in usable):
        score += 20
        reasons.append("Usable public post excerpt")
    if profile["website"]:
        score += 5
    followers = profile["followers"]
    if followers is None:
        reasons.append("Follower count unknown")
        if campaign["min_followers"] > 0 or campaign["max_followers"] < 10_000_000_000:
            blocked.append("followers_unknown")
    elif not campaign["min_followers"] <= followers <= campaign["max_followers"]:
        blocked.append("followers_out_of_range")
    else:
        score += 5
    language = profile.get("language")
    if language and campaign["languages"] and language not in campaign["languages"]:
        blocked.append("language_not_targeted")
    if language and language not in LANGUAGES:
        blocked.append("unsupported_language")
    if not language:
        language = campaign["language"]
        reasons.append("Language unverified; campaign default used for review")
    elif language in LANGUAGES:
        score += 5
    score = min(100, score)
    if score < campaign["min_score"]:
        blocked.append("below_min_score")
    return {"score": score, "reasons": reasons, "evidence": evidence,
            "niche": campaign["niche"], "eligible": not blocked,
            "blocked_reasons": blocked, "language": language,
            "matched_keywords": matched}


def _greeting(profile, campaign, language):
    name = profile["full_name"]
    # A display name is used verbatim only; no name is inferred from a handle.
    if (not name or len(name) > 40 or not re.fullmatch(r"[^\W\d_]+(?:[ '\u2019-][^\W\d_]+){0,5}", name)
            or INSTRUCTION_PATTERN.search(name)):
        name = ""
    word = "Hallo" if language == "de" and campaign["tone"] == "professional" else (
        "Hello" if language == "en" and campaign["tone"] == "professional" else "Hey")
    return word + (" " + name if name else "") + ", "


def generate_drafts(profile, analysis, campaign, provider="template"):
    """Return three local templates; [] for blocked profiles. No AI claim.

    Analysis is recomputed so callers cannot bypass eligibility with a stale or
    client-modified eligible flag. Evidence is selected from that fresh analysis.
    """
    if provider != "template":
        raise ValueError("engine.generate_drafts supports only the template provider")
    profile = normalize_profile(profile)
    campaign = validate_campaign(campaign)
    analysis = analyse_profile(profile, campaign)
    if not analysis["eligible"]:
        return []
    language = analysis["language"]
    candidates = [item for item in analysis["evidence"] if item["usable"]]
    candidates.sort(key=lambda e: (not any(_keyword_in(k, e["text"]) for k in campaign["include_keywords"]), e["kind"] != "bio"))
    evidence = candidates[0]
    questions = DEFAULT_QUESTIONS[language]
    for rule in campaign["hook_rules"]:
        if any(_keyword_in(keyword, evidence["text"]) for keyword in rule["keywords"]):
            questions = rule["questions"].get(language, questions)
            break
    location = ("deiner Bio" if evidence["kind"] == "bio" else "deinem Post") if language == "de" else (
        "your bio" if evidence["kind"] == "bio" else "your post")
    reference = ("in %s steht „%s“. " % (location, evidence["text"])) if language == "de" else (
        "%s says “%s”. " % (location, evidence["text"]))
    drafts = []
    for index, question in enumerate(questions):
        text = _greeting(profile, campaign, language) + reference + question
        violations = validate_draft(text, analysis, campaign, [evidence["id"]])
        if violations:
            return []
        drafts.append({"id": "template-" + str(index + 1),
                       "style": ("interest", "origin", "current_focus")[index],
                       "text": text, "evidence_ids": [evidence["id"]],
                       "methodology": "template-v1"})
    return drafts


def validate_draft(text, analysis, campaign, evidence_ids):
    """Conservative linting, not a truth oracle: human factual review is required.

    Every quotation must exactly match a cited usable evidence excerpt. At least
    one such quote is required. Regex rules cannot establish truth of free prose.
    """
    campaign = validate_campaign(campaign)
    violations = []
    if not isinstance(text, str) or not text.strip():
        return ["empty_draft"]
    if not isinstance(analysis, dict) or not isinstance(analysis.get("evidence"), list):
        return ["invalid_analysis"]
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
           or not isinstance(item.get("text"), str) for item in analysis["evidence"]):
        return ["invalid_analysis"]
    if len(text) > campaign["max_chars"]:
        violations.append("too_many_characters")
    if len(text.split()) > campaign["max_words"]:
        violations.append("too_many_words")
    if text.count("?") != 1:
        violations.append("exactly_one_question_required")
    if not text.rstrip().endswith("?"):
        violations.append("question_must_be_last")
    question = re.split(r"[.!]\s+|[“”]\s*[.!]?\s*", text.rstrip(" ?"))[-1].lstrip()
    if not OPEN_QUESTION.match(question):
        violations.append("open_question_required")
    if INSTRUCTION_PATTERN.search(text):
        violations.append("instruction_like_text")
    if FORBIDDEN_PATTERN.search(text):
        violations.append("unsupported_claim_or_pitch")
    if re.search(r"https?://|www\.|@[a-zA-Z0-9_]", text):
        violations.append("no_links_or_handles_in_opener")
    if any(unicodedata.category(c).startswith("C") for c in text if c not in "\n\t"):
        violations.append("control_characters")
    if not isinstance(analysis, dict) or not analysis.get("eligible"):
        violations.append("profile_not_eligible")
    evidence = {item["id"]: item for item in analysis.get("evidence", [])
                if isinstance(item, dict) and item.get("usable", True)} if isinstance(analysis, dict) else {}
    if (not isinstance(evidence_ids, list) or not evidence_ids
            or not all(isinstance(item, str) for item in evidence_ids)):
        violations.append("evidence_ids_required")
        cited = []
    else:
        if len(evidence_ids) != len(set(evidence_ids)):
            violations.append("duplicate_evidence_id")
        if any(item not in evidence for item in evidence_ids):
            violations.append("unknown_or_unusable_evidence_id")
        cited = [evidence[item]["text"] for item in evidence_ids if item in evidence]
    quotes = re.findall(r"„([^“]+)“|“([^”]+)”|\"([^\"]+)\"", text)
    quoted_texts = [next(part for part in group if part) for group in quotes]
    if not quoted_texts or not any(quote in cited for quote in quoted_texts):
        violations.append("exact_evidence_quote_required")
    if any(quote not in cited for quote in quoted_texts):
        violations.append("unsupported_quote")
    if any(item not in quoted_texts for item in cited):
        violations.append("unused_evidence_citation")
    return list(dict.fromkeys(violations))
