"""AICore testləri. Real API açarı və internet tələb etmir (provayder saxtalaşdırılır).

İşə salmaq:  python -m unittest discover -s tests -v
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as aicore  # noqa: E402
from providers import ProviderError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class FakeProvider:
    name = "Fake"
    key_env = "FAKE_API_KEY"
    model = "fake-model"

    def __init__(self, chunks=("Salam, ", "dünya!"), error=None):
        self.chunks, self.error, self.calls = list(chunks), error, []

    def stream(self, system, messages):
        # tarixçə sonradan dəyişəcəyi üçün surət saxlanılır
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages))})
        for c in self.chunks:
            yield c
        if self.error:
            raise self.error


def parse_sse(raw):
    return [json.loads(b[6:]) for b in raw.split("\n\n") if b.startswith("data: ")]


class AppTestCase(unittest.TestCase):
    def setUp(self):
        aicore._conversations.clear()
        aicore._hits.clear()
        aicore.RATE_LIMIT_PER_MINUTE = 1000
        self.fake = FakeProvider()
        aicore._provider = self.fake
        self.http = aicore.app.test_client()

    def tearDown(self):
        aicore._provider = None

    def chat(self, message="", images=(), client=None):
        data = {"message": message}
        if images:
            data["images"] = [(io.BytesIO(b), name) for name, b in images]
        return (client or self.http).post("/api/chat", data=data, content_type="multipart/form-data")


class ChatTests(AppTestCase):
    def test_home_page_loads(self):
        r = self.http.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("AICore", r.get_data(as_text=True))

    def test_health_reports_configured(self):
        data = self.http.get("/api/health").get_json()
        self.assertTrue(data["configured"])
        self.assertEqual(data["provider"], "Fake")

    def test_no_key_gives_503(self):
        aicore._provider = None
        saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY")}
        os.environ["PROVIDER"] = "gemini"
        try:
            self.assertFalse(self.http.get("/api/health").get_json()["configured"])
            self.assertEqual(self.chat("salam").status_code, 503)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_placeholder_key_is_treated_as_missing(self):
        aicore._provider = None
        os.environ["PROVIDER"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "buraya-oz-acarinizi-yazin"
        try:
            self.assertEqual(self.chat("salam").status_code, 503)
        finally:
            del os.environ["GEMINI_API_KEY"]

    def test_text_chat_streams_and_saves_history(self):
        r = self.chat("Salam")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/event-stream")
        events = parse_sse(r.get_data(as_text=True))
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "delta"), "Salam, dünya!")
        self.assertEqual(events[-1]["type"], "done")

        self.chat("Davam et").get_data()
        second = self.fake.calls[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertEqual(second[1]["content"], "Salam, dünya!")

    def test_system_prompt_is_sent(self):
        self.chat("test").get_data()
        system = self.fake.calls[0]["system"]
        self.assertIn("AICore", system)
        self.assertIn("ŞƏKİL ANALİZİ", system)

    def test_empty_message_rejected(self):
        self.assertEqual(self.chat("   ").status_code, 400)

    def test_too_long_message_rejected(self):
        self.assertEqual(self.chat("a" * (aicore.MAX_MESSAGE_CHARS + 1)).status_code, 400)

    def test_reset_clears_history(self):
        self.chat("Salam").get_data()
        self.assertEqual(self.http.post("/api/reset").status_code, 200)
        self.chat("Yenidən").get_data()
        self.assertEqual(len(self.fake.calls[1]["messages"]), 1)

    def test_sessions_are_isolated(self):
        other = aicore.app.test_client()
        self.chat("Birinci istifadəçi").get_data()
        self.chat("İkinci istifadəçi", client=other).get_data()
        self.assertEqual(len(self.fake.calls[1]["messages"]), 1)

    def test_rate_limit(self):
        aicore.RATE_LIMIT_PER_MINUTE = 2
        aicore._hits.clear()
        self.assertEqual(self.chat("1").status_code, 200)
        self.assertEqual(self.chat("2").status_code, 200)
        self.assertEqual(self.chat("3").status_code, 429)


class ImageTests(AppTestCase):
    def test_image_with_default_prompt(self):
        self.chat("", images=[("a.png", PNG)]).get_data()
        content = self.fake.calls[0]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["media_type"], "image/png")
        self.assertEqual(content[-1], {"type": "text", "text": aicore.DEFAULT_IMAGE_PROMPT})

    def test_image_with_question(self):
        self.chat("Bu nədir?", images=[("a.jpg", JPEG)]).get_data()
        content = self.fake.calls[0]["messages"][0]["content"]
        self.assertEqual(content[0]["media_type"], "image/jpeg")
        self.assertEqual(content[-1]["text"], "Bu nədir?")

    def test_fake_image_rejected(self):
        self.assertEqual(self.chat("bax", images=[("evil.png", b"<script>alert(1)</script>")]).status_code, 400)

    def test_too_many_images_rejected(self):
        imgs = [(f"{i}.png", PNG) for i in range(aicore.MAX_IMAGES + 1)]
        self.assertEqual(self.chat("bax", images=imgs).status_code, 400)

    def test_oversized_image_rejected(self):
        big = PNG + b"\x00" * (aicore.MAX_IMAGE_BYTES + 10)
        self.assertEqual(self.chat("bax", images=[("big.png", big)]).status_code, 413)

    def test_old_images_are_pruned(self):
        self.chat("", images=[("1.png", PNG)]).get_data()
        self.chat("", images=[("2.png", PNG)]).get_data()
        msgs = list(aicore._conversations.values())[0].messages
        with_images = [m for m in msgs if m["role"] == "user" and any(b.get("type") == "image" for b in m["content"])]
        self.assertEqual(len(with_images), 1)
        self.assertIn("yaddaşdan çıxarılıb", json.dumps(msgs, ensure_ascii=False))

    def test_detect_image_type(self):
        self.assertEqual(aicore.detect_image_type(PNG), "image/png")
        self.assertEqual(aicore.detect_image_type(JPEG), "image/jpeg")
        self.assertEqual(aicore.detect_image_type(b"GIF89a" + b"0" * 20), "image/gif")
        self.assertEqual(aicore.detect_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")
        self.assertIsNone(aicore.detect_image_type(b"hello world"))


class ErrorTests(AppTestCase):
    def test_auth_error_is_friendly_and_history_rolled_back(self):
        self.fake.chunks = []
        self.fake.error = ProviderError("auth", "SECRET technical detail")
        events = parse_sse(self.chat("Salam").get_data(as_text=True))
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("FAKE_API_KEY", events[-1]["message"])
        self.assertNotIn("SECRET", events[-1]["message"])  # texniki detal istifadəçiyə sızmır

        self.fake.error, self.fake.chunks = None, ["ok"]
        self.chat("Yenidən").get_data()
        self.assertEqual(len(self.fake.calls[-1]["messages"]), 1)  # uğursuz sual tarixçədə qalmayıb

    def test_rate_limit_message_mentions_free_limit(self):
        self.fake.chunks, self.fake.error = [], ProviderError("rate_limit")
        events = parse_sse(self.chat("Salam").get_data(as_text=True))
        self.assertIn("Pulsuz limit", events[-1]["message"])

    def test_unexpected_exception_is_generic(self):
        self.fake.chunks, self.fake.error = [], RuntimeError("boom")
        events = parse_sse(self.chat("Salam").get_data(as_text=True))
        self.assertEqual(events[-1]["type"], "error")
        self.assertNotIn("boom", events[-1]["message"])

    def test_error_after_partial_reply_keeps_partial(self):
        self.fake.chunks, self.fake.error = ["yarım cavab"], ProviderError("server")
        events = parse_sse(self.chat("Salam").get_data(as_text=True))
        self.assertEqual(events[0]["type"], "delta")
        self.assertEqual(events[-1]["type"], "error")

        self.fake.error = None
        self.chat("Davam").get_data()
        roles = [m["role"] for m in self.fake.calls[-1]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])


class HistoryTests(unittest.TestCase):
    def test_trim_history_keeps_user_first_and_limit(self):
        msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(40)]
        aicore.trim_history(msgs)
        self.assertLessEqual(len(msgs), aicore.MAX_HISTORY_MESSAGES)
        self.assertEqual(msgs[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
