"""AI provayderləri: Gemini (pulsuz, standart) və Claude (istəyə bağlı).

Daxili mesaj formatı provayderdən asılı deyil:
    {"role": "user" | "assistant", "content": str | list[block]}
    block = {"type": "text", "text": ...}
          | {"type": "image", "media_type": "image/png", "data": "<base64>"}

Hər provayder `stream(system, messages)` generatoru təqdim edir. O, mətn parçaları
qaytarır və problem olanda `ProviderError` atır.
"""

import json
import logging
import os
import socket
import urllib.error
import urllib.request

log = logging.getLogger("aicore.providers")

MAX_TOKENS_NOTICE = "\n\n*(Cavab uzunluq limitinə çatdığı üçün yarımçıq kəsildi. “Davam et” yazın.)*"


class ProviderError(Exception):
    """kind: auth | permission | rate_limit | connection | bad_request | not_found |
    blocked | region | credit | server | unknown"""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


# --------------------------------------------------------------------------
# Format çevirmələri
# --------------------------------------------------------------------------
def to_gemini_contents(messages):
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        if isinstance(m["content"], str):
            blocks = [{"type": "text", "text": m["content"]}]
        else:
            blocks = m["content"]
        parts = []
        for b in blocks:
            if b["type"] == "image":
                parts.append({"inlineData": {"mimeType": b["media_type"], "data": b["data"]}})
            elif b.get("text"):
                parts.append({"text": b["text"]})
        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


def to_claude_messages(messages):
    out = []
    for m in messages:
        if isinstance(m["content"], str):
            out.append({"role": m["role"], "content": m["content"]})
            continue
        blocks = []
        for b in m["content"]:
            if b["type"] == "image":
                blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": b["media_type"], "data": b["data"]},
                    }
                )
            elif b.get("text"):
                blocks.append({"type": "text", "text": b["text"]})
        out.append({"role": m["role"], "content": blocks})
    return out


# --------------------------------------------------------------------------
# Gemini (Google AI Studio) – pulsuz səviyyə, əlavə paket tələb etmir
# --------------------------------------------------------------------------
class GeminiProvider:
    name = "Gemini"
    key_env = "GEMINI_API_KEY"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"

    def __init__(self, api_key, models, max_tokens=4096, timeout=60):
        self.api_key = api_key
        self.models = list(models)  # birinci model əsasdır, qalanları ehtiyat
        self.max_tokens = max_tokens
        self.timeout = timeout

    @property
    def model(self):
        return self.models[0]

    def stream(self, system, messages):
        for i, model in enumerate(self.models):
            started = False
            try:
                for chunk in self._stream_model(model, system, messages):
                    if not started and i > 0:
                        # işləyən ehtiyat modeli yadda saxla
                        self.models.insert(0, self.models.pop(i))
                    started = True
                    yield chunk
                return
            except ProviderError as exc:
                if exc.kind == "not_found" and not started and i < len(self.models) - 1:
                    log.warning("Model tapılmadı: %s. Növbəti model yoxlanılır.", model)
                    continue
                raise

    def _stream_model(self, model, system, messages):
        body = {
            "contents": to_gemini_contents(messages),
            "generationConfig": {"maxOutputTokens": self.max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        request = urllib.request.Request(
            self.URL.format(model=model),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )

        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            raise ProviderError("connection", str(exc)) from None

        got_text = False
        block_reason = None
        finish = None

        with response:
            try:
                for raw in response:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        data = json.loads(payload)
                    except ValueError:
                        continue

                    if isinstance(data.get("error"), dict):
                        raise self._error_from_body(data["error"].get("code", 0), data["error"])

                    feedback = data.get("promptFeedback") or {}
                    if feedback.get("blockReason"):
                        block_reason = feedback["blockReason"]

                    for candidate in data.get("candidates") or []:
                        for part in (candidate.get("content") or {}).get("parts") or []:
                            if part.get("thought"):
                                continue  # modelin daxili düşüncələri göstərilmir
                            text = part.get("text")
                            if text:
                                got_text = True
                                yield text
                        if candidate.get("finishReason"):
                            finish = candidate["finishReason"]
            except (socket.timeout, TimeoutError, ConnectionError, urllib.error.URLError) as exc:
                raise ProviderError("connection", str(exc)) from None

        if not got_text:
            raise ProviderError("blocked", block_reason or finish or "boş cavab")
        if finish == "MAX_TOKENS":
            yield MAX_TOKENS_NOTICE

    @staticmethod
    def _error_from_body(status, error):
        message = str(error.get("message", ""))
        low = message.lower()
        status = int(status or 0)
        if "api key not valid" in low or "api_key_invalid" in json.dumps(error).lower():
            return ProviderError("auth", message)
        if "location is not supported" in low:
            return ProviderError("region", message)
        if status == 429 or error.get("status") == "RESOURCE_EXHAUSTED":
            return ProviderError("rate_limit", message)
        if status == 401:
            return ProviderError("auth", message)
        if status == 403:
            return ProviderError("permission", message)
        if status == 404:
            return ProviderError("not_found", message)
        if status == 400:
            return ProviderError("bad_request", message)
        if status >= 500:
            return ProviderError("server", message)
        return ProviderError("unknown", message)

    def _http_error(self, exc):
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            error = body.get("error", {}) if isinstance(body, dict) else {}
        except Exception:  # noqa: BLE001
            error = {}
        if not isinstance(error, dict):
            error = {}
        return self._error_from_body(exc.code, error)


# --------------------------------------------------------------------------
# Claude (pullu, istəyə bağlı) – yalnız PROVIDER=claude olanda "anthropic" paketi lazımdır
# --------------------------------------------------------------------------
class ClaudeProvider:
    name = "Claude"
    key_env = "ANTHROPIC_API_KEY"

    def __init__(self, api_key, model, max_tokens=4096):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Claude üçün 'pip install anthropic' əmrini işlədin.") from exc
        self._a = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def stream(self, system, messages):
        a = self._a
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=to_claude_messages(messages),
            ) as stream:
                for chunk in stream.text_stream:
                    yield chunk
        except a.AuthenticationError as exc:
            raise ProviderError("auth", str(exc)) from None
        except a.PermissionDeniedError as exc:
            raise ProviderError("permission", str(exc)) from None
        except a.RateLimitError as exc:
            raise ProviderError("rate_limit", str(exc)) from None
        except a.APIConnectionError as exc:
            raise ProviderError("connection", str(exc)) from None
        except a.BadRequestError as exc:
            low = str(exc).lower()
            kind = "credit" if ("credit" in low or "balance" in low) else "bad_request"
            raise ProviderError(kind, str(exc)) from None
        except a.APIStatusError as exc:
            raise ProviderError("server", str(exc)) from None


# --------------------------------------------------------------------------
# Seçim
# --------------------------------------------------------------------------
def _placeholder(value):
    return not value or "buraya" in value.lower()


def make_provider():
    """.env-dəki PROVIDER-ə görə provayder yaradır. Açar yoxdursa None qaytarır."""
    name = os.getenv("PROVIDER", "gemini").strip().lower()
    max_tokens = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))

    if name == "claude":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if _placeholder(key):
            return None
        return ClaudeProvider(key, os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens)

    if name == "gemini":
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if _placeholder(key):
            return None
        primary = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
        fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash,gemini-flash-lite-latest").split(",")]
        models = []
        for m in [primary] + fallbacks:
            if m and m not in models:
                models.append(m)
        return GeminiProvider(key, models, max_tokens)

    raise ValueError("PROVIDER dəyəri 'gemini' və ya 'claude' olmalıdır.")
