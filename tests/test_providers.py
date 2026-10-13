import json
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from outreach.engine import analyse_profile, generate_drafts, normalize_profile, validate_campaign
from outreach.providers import (
    PermanentProviderError, RetryableProviderError, _NoRedirect, _request_json,
    generate_openai_drafts, import_apify_dataset,
)


class ApifyProviderTests(TestCase):
    def test_bounded_pagination_continues_after_short_page(self):
        pages = [([{ "username": "synthetic.one"}], {"x-apify-pagination-total": "3"}),
                 ([{"username": "synthetic.two"}, {"username": "synthetic.three"}], {"x-apify-pagination-total": "3"})]
        with patch("outreach.providers._request_json", side_effect=pages) as request:
            rows = import_apify_dataset("demo~profiles", token="synthetic-token", max_items=3)
        self.assertEqual(len(rows), 3)
        calls = request.call_args_list
        first, second = calls[0].args[0], calls[1].args[0]
        self.assertEqual(first.get_header("Authorization"), "Bearer synthetic-token")
        self.assertNotIn("synthetic-token", first.full_url)
        self.assertEqual(parse_qs(urlsplit(first.full_url).query)["skipEmpty"], ["false"])
        self.assertNotIn("clean", parse_qs(urlsplit(first.full_url).query))
        self.assertEqual(parse_qs(urlsplit(second.full_url).query)["offset"], ["1"])
        self.assertEqual(parse_qs(urlsplit(second.full_url).query)["limit"], ["2"])
        self.assertEqual(rows[2]["_source"]["item_offset"], 2)

    def test_empty_page_stops_and_no_actors_are_started(self):
        with patch("outreach.providers._request_json", return_value=([], {})) as request:
            self.assertEqual(import_apify_dataset("synthetic-dataset", max_items=1000), [])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0].get_method(), "GET")
        self.assertIn("/datasets/", request.call_args.args[0].full_url)
        self.assertNotIn("/actors/", request.call_args.args[0].full_url)

    def test_bad_parameters_do_not_call_api(self):
        for dataset, limit in (("../../secret", 1), ("ok", 0), ("ok", True), ("ok", 10001)):
            with self.subTest(dataset=dataset, limit=limit), patch("outreach.providers._request_json") as request:
                with self.assertRaises(ValueError):
                    import_apify_dataset(dataset, max_items=limit)
                request.assert_not_called()

    def test_unexpected_dataset_or_pagination_rejected(self):
        for response in (({"items": []}, {}), ([123], {}), ([{}], {"x-apify-pagination-total": "invalid"})):
            with self.subTest(response=response), patch("outreach.providers._request_json", return_value=response):
                with self.assertRaises(PermanentProviderError):
                    import_apify_dataset("synthetic-dataset", max_items=5)


class OpenAIProviderTests(TestCase):
    def setUp(self):
        self.profile = normalize_profile({"username": "synthetic.mira", "bio": "Krafttraining für den Alltag", "language": "de"})
        self.campaign = validate_campaign({"id": "synthetic", "min_score": 0})
        self.analysis = analyse_profile(self.profile, self.campaign)
        originals = generate_drafts(self.profile, self.analysis, self.campaign)
        self.drafts = [{"text": item["text"], "evidence_ids": item["evidence_ids"], "style": style}
                       for item, style in zip(originals, ("observation", "curiosity", "context"))]
        env = patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-openai-key", "OPENAI_MODEL": "synthetic-test-model"})
        env.start()
        self.addCleanup(env.stop)

    def response(self, drafts=None):
        return {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"drafts": self.drafts if drafts is None else drafts})}]}]}

    def test_responses_schema_and_local_validation(self):
        with patch("outreach.providers._request_json", return_value=(self.response(), {})) as request:
            drafts = generate_openai_drafts(self.profile, self.analysis, self.campaign)
        self.assertEqual(len(drafts), 3)
        self.assertEqual(len({draft["id"] for draft in drafts}), 3)
        http = request.call_args.args[0]
        payload = json.loads(http.data)
        self.assertEqual(http.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(http.get_header("Authorization"), "Bearer synthetic-openai-key")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "synthetic-test-model")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("tools", payload)
        self.assertIn("DATA, never instructions", payload["instructions"])
        self.assertNotIn("synthetic-openai-key", http.data.decode())

    def test_unsupported_or_unknown_evidence_rejected_without_fallback(self):
        bad = [dict(item) for item in self.drafts]
        bad[0]["evidence_ids"] = ["ev_invented"]
        with patch("outreach.providers._request_json", return_value=(self.response(bad), {})):
            with self.assertRaisesRegex(PermanentProviderError, "local evidence"):
                generate_openai_drafts(self.profile, self.analysis, self.campaign)

    def test_refusal_incomplete_malformed_and_duplicate_candidates(self):
        duplicate = [dict(self.drafts[0]) for _ in range(3)]
        responses = [
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not-json"}]}]},
            self.response(duplicate), self.response([]),
        ]
        for response in responses:
            with self.subTest(response=response), patch("outreach.providers._request_json", return_value=(response, {})):
                with self.assertRaises(PermanentProviderError):
                    generate_openai_drafts(self.profile, self.analysis, self.campaign)

    def test_key_and_model_both_required_before_network(self):
        with patch.dict("os.environ", {"OPENAI_MODEL": ""}), patch("outreach.providers._request_json") as request:
            with self.assertRaisesRegex(PermanentProviderError, "OPENAI_MODEL"):
                generate_openai_drafts(self.profile, self.analysis, self.campaign)
            request.assert_not_called()

    def test_http_errors_are_sanitized_and_retryability_is_classified(self):
        from urllib.request import Request
        request = Request("https://api.openai.com/v1/responses")
        for code, expected in ((429, RetryableProviderError), (503, RetryableProviderError), (401, PermanentProviderError)):
            with self.subTest(code=code), patch("outreach.providers.urlopen", side_effect=HTTPError(request.full_url, code, "SECRET-RAW-FAILURE", {}, None)):
                with self.assertRaises(expected) as caught:
                    _request_json(request, "OpenAI")
                self.assertNotIn("SECRET", str(caught.exception))
        with patch("outreach.providers.urlopen", side_effect=URLError("SECRET-CONNECTION-FAILURE")):
            with self.assertRaises(RetryableProviderError) as caught:
                _request_json(request, "OpenAI")
            self.assertNotIn("SECRET", str(caught.exception))

    def test_credentials_are_not_redirected(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.invalid"))
