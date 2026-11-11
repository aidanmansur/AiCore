"""Gemini və Claude provayderlərinin testləri (şəbəkə saxtalaşdırılır)."""

import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers  # noqa: E402
from providers import GeminiProvider, ProviderError  # noqa: E402

MESSAGES = [
    {"role": "user", "content": [
        {"type": "image", "media_type": "image/png", "data": "QUJD"},
        {"type": "text", "text": "Bu nədir?"},
    ]},
    {"role": "assistant", "content": "Bu bir şəkildir."},
    {"role": "user", "content": [{"type": "text", "text": "Daha ətraflı"}]},
]


def sse_line(obj):
    return ("data: " + json.dumps(obj) + "\r\n").encode()


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def text_chunk(text, finish=None):
    cand = {"content": {"parts": [{"text": text}]}}
    if finish:
        cand["finishReason"] = finish
    return sse_line({"candidates": [cand]})


def http_error(code, body):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


class GeminiTests(unittest.TestCase):
    def make(self, models=("m1", "m2")):
        return GeminiProvider("KEY123", models, max_tokens=999)

    def run_stream(self, provider, urlopen):
        with mock.patch.object(providers.urllib.request, "urlopen", urlopen):
            return "".join(provider.stream("SYSTEM", MESSAGES))

    def test_streams_text_and_builds_request(self):
        captured = {}

        def urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["body"] = json.loads(req.data)
            return FakeResponse([text_chunk("Sa"), b"\r\n", text_chunk("lam", "STOP")])

        out = self.run_stream(self.make(), urlopen)
        self.assertEqual(out, "Salam")
        self.assertIn("models/m1:streamGenerateContent?alt=sse", captured["url"])
        self.assertEqual(captured["headers"]["x-goog-api-key"], "KEY123")
        self.assertNotIn("KEY123", captured["url"])  # açar URL-də yox, başlıqda göndərilir
        body = captured["body"]
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "SYSTEM")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 999)
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])
        first = body["contents"][0]["parts"]
        self.assertEqual(first[0], {"inlineData": {"mimeType": "image/png", "data": "QUJD"}})
        self.assertEqual(first[1], {"text": "Bu nədir?"})

    def test_thought_parts_are_hidden(self):
        line = sse_line({"candidates": [{"content": {"parts": [
            {"text": "daxili düşüncə", "thought": True}, {"text": "cavab"}]}}]})
        self.assertEqual(self.run_stream(self.make(), lambda *a, **k: FakeResponse([line])), "cavab")

    def test_max_tokens_adds_notice(self):
        out = self.run_stream(self.make(), lambda *a, **k: FakeResponse([text_chunk("yarım", "MAX_TOKENS")]))
        self.assertTrue(out.startswith("yarım"))
        self.assertIn("yarımçıq", out)

    def test_blocked_prompt(self):
        line = sse_line({"promptFeedback": {"blockReason": "SAFETY"}})
        with self.assertRaises(ProviderError) as cm:
            self.run_stream(self.make(), lambda *a, **k: FakeResponse([line]))
        self.assertEqual(cm.exception.kind, "blocked")

    def test_invalid_key(self):
        err = http_error(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                          "status": "INVALID_ARGUMENT"}})
        with self.assertRaises(ProviderError) as cm:
            self.run_stream(self.make(), mock.Mock(side_effect=err))
        self.assertEqual(cm.exception.kind, "auth")

    def test_rate_limit(self):
        err = http_error(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        with self.assertRaises(ProviderError) as cm:
            self.run_stream(self.make(), mock.Mock(side_effect=err))
        self.assertEqual(cm.exception.kind, "rate_limit")

    def test_region_error(self):
        err = http_error(400, {"error": {"code": 400, "message": "User location is not supported for the API use."}})
        with self.assertRaises(ProviderError) as cm:
            self.run_stream(self.make(), mock.Mock(side_effect=err))
        self.assertEqual(cm.exception.kind, "region")

    def test_connection_error(self):
        with self.assertRaises(ProviderError) as cm:
            self.run_stream(self.make(), mock.Mock(side_effect=urllib.error.URLError("no network")))
        self.assertEqual(cm.exception.kind, "connection")

    def test_fallback_model_on_404_and_remembered(self):
        calls = []

        def urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "models/m1:" in req.full_url:
                raise http_error(404, {"error": {"code": 404, "message": "model not found"}})
            return FakeResponse([text_chunk("ok", "STOP")])

        provider = self.make()
        self.assertEqual(self.run_stream(provider, urlopen), "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(provider.model, "m2")  # işləyən model əsas seçilir

        calls.clear()
        self.assertEqual(self.run_stream(provider, urlopen), "ok")
        self.assertEqual(len(calls), 1)  # növbəti sorğuda artıq m1 yoxlanılmır

    def test_all_models_missing_raises(self):
        err = lambda *a, **k: (_ for _ in ()).throw(http_error(404, {"error": {"code": 404, "message": "x"}}))  # noqa: E731
        with self.assertRaises(ProviderError) as cm:
            self.run_stream(self.make(), err)
        self.assertEqual(cm.exception.kind, "not_found")


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "ANTHROPIC_API_KEY")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_is_gemini(self):
        os.environ.pop("PROVIDER", None)
        os.environ["GEMINI_API_KEY"] = "abc"
        os.environ.pop("GEMINI_MODEL", None)
        p = providers.make_provider()
        self.assertEqual(p.name, "Gemini")
        self.assertEqual(p.models[0], "gemini-flash-latest")
        self.assertEqual(len(p.models), len(set(p.models)))

    def test_missing_key_returns_none(self):
        os.environ["PROVIDER"] = "gemini"
        os.environ.pop("GEMINI_API_KEY", None)
        self.assertIsNone(providers.make_provider())

    def test_invalid_provider(self):
        os.environ["PROVIDER"] = "nope"
        with self.assertRaises(ValueError):
            providers.make_provider()


class ConversionTests(unittest.TestCase):
    def test_claude_conversion(self):
        out = providers.to_claude_messages(MESSAGES)
        self.assertEqual(out[0]["content"][0]["source"],
                         {"type": "base64", "media_type": "image/png", "data": "QUJD"})
        self.assertEqual(out[1], {"role": "assistant", "content": "Bu bir şəkildir."})


if __name__ == "__main__":
    unittest.main()
