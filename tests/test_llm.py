import unittest

from .helpers import MockServer
from rjh.http import HttpClient, RateLimiter
from rjh.llm import (AnthropicProvider, GeminiProvider, LLMAuthError, LLMConfig, LLMError, ManualProvider,
                     ManualStepRequired, ModelUnavailableError, OpenAICompatProvider, create_provider)
from rjh.profile import LLMSettings


def fast_http():
    return HttpClient(limiter=RateLimiter(0), sleep=lambda s: None, max_retries=2)


GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}, "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5}}


class Gemini(unittest.TestCase):
    def cfg(self, srv, model="gemini-test"):
        return LLMConfig(provider="gemini", model=model, api_key="KEY-123", base_url=srv.url + "/v1beta")

    def test_wire_format(self):
        for model in ("gemini-test", "models/gemini-test"):          # avec ou sans préfixe : même chemin
            with MockServer(lambda r: (200, GEMINI_OK)) as srv:
                resp = GeminiProvider(self.cfg(srv, model), fast_http()).complete("SYS", "USER")
                req = srv.requests[0]
                self.assertEqual(req["path"], "/v1beta/models/gemini-test:generateContent")
                self.assertEqual(req["headers"]["x-goog-api-key"], "KEY-123")
                self.assertNotIn("KEY-123", req["path"] + req["query"])           # jamais dans l'URL
                self.assertEqual(req["body"]["systemInstruction"]["parts"][0]["text"], "SYS")
                self.assertEqual(req["body"]["contents"][0]["parts"][0]["text"], "USER")
                self.assertEqual(req["body"]["generationConfig"]["responseMimeType"], "application/json")
                self.assertEqual((resp.text, resp.usage.input_tokens, resp.usage.output_tokens), ('{"ok":true}', 12, 5))
                self.assertFalse(resp.truncated)

    def test_truncation_flag(self):
        data = {**GEMINI_OK, "candidates": [{"content": {"parts": [{"text": "{"}]}, "finishReason": "MAX_TOKENS"}]}
        with MockServer(lambda r: (200, data)) as srv:
            self.assertTrue(GeminiProvider(self.cfg(srv), fast_http()).complete("s", "u").truncated)

    def test_retired_model_gives_an_actionable_error(self):
        with MockServer(lambda r: (404, {"error": {"message": "model not found"}})) as srv:
            with self.assertRaises(ModelUnavailableError) as cm:
                GeminiProvider(self.cfg(srv, "gemini-2.5-flash"), fast_http()).complete("s", "u")
            self.assertIn("gemini-2.5-flash", str(cm.exception))
            self.assertIn("check()", str(cm.exception))
            self.assertEqual(len(srv.requests), 1)                  # pas de retry sur un 404

    def test_invalid_key_is_an_auth_error(self):
        with MockServer(lambda r: (400, {"error": {"message": "API key not valid. Please pass a valid API key."}})) as srv:
            with self.assertRaises(LLMAuthError):
                GeminiProvider(self.cfg(srv), fast_http()).complete("s", "u")

    def test_blocked_prompt_is_an_error_not_an_empty_string(self):
        with MockServer(lambda r: (200, {"promptFeedback": {"blockReason": "SAFETY"}})) as srv:
            with self.assertRaisesRegex(LLMError, "SAFETY"):
                GeminiProvider(self.cfg(srv), fast_http()).complete("s", "u")

    def test_429_is_retried(self):
        n = {"i": 0}

        def handler(r):
            n["i"] += 1
            return (429, {"error": "slow"}, {"Retry-After": "0"}) if n["i"] == 1 else (200, GEMINI_OK)
        with MockServer(handler) as srv:
            self.assertEqual(GeminiProvider(self.cfg(srv), fast_http()).complete("s", "u").text, '{"ok":true}')
            self.assertEqual(n["i"], 2)

    def test_check_explains_a_retired_model_and_suggests_replacements(self):
        models = {"models": [
            {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.5-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
        ]}
        with MockServer(lambda r: (200, models)) as srv:
            bad = GeminiProvider(self.cfg(srv, "gemini-2.5-flash"), fast_http()).check()
            self.assertFalse(bad.ok)
            self.assertIn("gemini-3.5-flash", bad.suggestions)
            self.assertNotIn("embedding-001", bad.suggestions)
            self.assertTrue(GeminiProvider(self.cfg(srv, "gemini-3.5-flash"), fast_http()).check().ok)
            self.assertEqual(srv.requests[0]["headers"]["x-goog-api-key"], "KEY-123")

    def test_check_without_key(self):
        c = GeminiProvider(LLMConfig(provider="gemini", model="m")).check()
        self.assertFalse(c.ok)


OPENAI_OK = {"choices": [{"message": {"content": '{"ok":1}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class OpenAICompat(unittest.TestCase):
    def provider(self, srv, name="openai", key="sk-test", needs_key=True):
        return OpenAICompatProvider(LLMConfig(provider=name, model="m1", api_key=key, base_url=srv.url + "/v1"), fast_http(), needs_key=needs_key)

    def test_wire_format(self):
        with MockServer(lambda r: (200, OPENAI_OK)) as srv:
            resp = self.provider(srv).complete("SYS", "USER")
            r = srv.requests[0]
            self.assertEqual(r["path"], "/v1/chat/completions")
            self.assertEqual(r["headers"]["authorization"], "Bearer sk-test")
            self.assertEqual([m["role"] for m in r["body"]["messages"]], ["system", "user"])
            self.assertEqual(r["body"]["response_format"], {"type": "json_object"})
            self.assertIn("max_tokens", r["body"])                    # serveur local : max_tokens
            self.assertEqual((resp.text, resp.usage.input_tokens, resp.usage.output_tokens), ('{"ok":1}', 7, 3))

    def test_ollama_needs_no_key_and_sends_no_auth_header(self):
        with MockServer(lambda r: (200, OPENAI_OK)) as srv:
            self.provider(srv, "ollama", key="", needs_key=False).complete("s", "u")
            self.assertNotIn("authorization", srv.requests[0]["headers"])

    def test_server_rejecting_response_format_is_handled(self):
        def handler(r):
            if "response_format" in r["body"]:
                return 400, {"error": {"message": "Unsupported parameter: 'response_format'"}}
            return 200, OPENAI_OK
        with MockServer(handler) as srv:
            self.assertEqual(self.provider(srv).complete("s", "u").text, '{"ok":1}')
            self.assertEqual(len(srv.requests), 2)
            self.assertNotIn("response_format", srv.requests[1]["body"])

    def test_max_tokens_is_renamed_when_the_server_demands_it(self):
        def handler(r):
            if "max_tokens" in r["body"]:
                return 400, {"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}}
            return 200, OPENAI_OK
        with MockServer(handler) as srv:
            self.provider(srv).complete("s", "u")
            self.assertIn("max_completion_tokens", srv.requests[1]["body"])

    def test_length_finish_reason_flags_truncation(self):
        data = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
        with MockServer(lambda r: (200, data)) as srv:
            self.assertTrue(self.provider(srv).complete("s", "u").truncated)

    def test_401_is_an_auth_error_and_404_a_model_error(self):
        with MockServer(lambda r: (401, {"error": "bad key"})) as srv:
            with self.assertRaises(LLMAuthError):
                self.provider(srv).complete("s", "u")
        with MockServer(lambda r: (404, {"error": "no such model"})) as srv:
            with self.assertRaises(ModelUnavailableError):
                self.provider(srv).complete("s", "u")

    def test_model_listing(self):
        with MockServer(lambda r: (200, {"data": [{"id": "m1"}, {"id": "m2"}]})) as srv:
            self.assertEqual(self.provider(srv).list_models(), ["m1", "m2"])
            self.assertTrue(self.provider(srv).check().ok)


class Anthropic(unittest.TestCase):
    def test_wire_format(self):
        data = {"content": [{"type": "text", "text": '{"a":1}'}], "usage": {"input_tokens": 9, "output_tokens": 4}, "stop_reason": "end_turn"}
        with MockServer(lambda r: (200, data)) as srv:
            p = AnthropicProvider(LLMConfig(provider="anthropic", model="m", api_key="ak", base_url=srv.url + "/v1"), fast_http())
            resp = p.complete("SYS", "USER")
            r = srv.requests[0]
            self.assertEqual(r["path"], "/v1/messages")
            self.assertEqual(r["headers"]["x-api-key"], "ak")
            self.assertEqual(r["headers"]["anthropic-version"], "2023-06-01")
            self.assertEqual(r["body"]["system"], "SYS")
            self.assertEqual(r["body"]["messages"], [{"role": "user", "content": "USER"}])
            self.assertEqual((resp.text, resp.usage.input_tokens), ('{"a":1}', 9))
            self.assertNotIn("response_format", r["body"])


class ManualAndFactory(unittest.TestCase):
    def test_manual_provider_raises_with_the_prompt(self):
        with self.assertRaises(ManualStepRequired) as cm:
            ManualProvider(LLMConfig()).complete("SYS", "USER")
        self.assertEqual(cm.exception.prompt, "SYS\n\nUSER")
        self.assertTrue(ManualProvider.interactive)

    def test_factory_routes_to_the_right_class(self):
        env = {"K": "secret"}
        self.assertIsInstance(create_provider({"provider": "manual"}, env=env), ManualProvider)
        self.assertIsInstance(create_provider({"provider": "gemini", "model": "m", "api_key_env": "K"}, env=env), GeminiProvider)
        self.assertIsInstance(create_provider({"provider": "anthropic", "model": "m", "api_key_env": "K"}, env=env), AnthropicProvider)
        oa = create_provider({"provider": "openai", "model": "m", "api_key_env": "K"}, env=env)
        self.assertIsInstance(oa, OpenAICompatProvider)
        self.assertEqual(oa.cfg.base_url, "https://api.openai.com/v1")
        ol = create_provider({"provider": "ollama", "model": "llama"}, env={})
        self.assertFalse(ol.needs_key)
        self.assertEqual(ol.cfg.base_url, "http://localhost:11434/v1")
        custom = create_provider({"provider": "monserveur", "model": "m", "base_url": "http://x/v1"}, env={})
        self.assertIsInstance(custom, OpenAICompatProvider)
        self.assertFalse(custom.needs_key)
        with self.assertRaises(LLMAuthError):                       # api_key_env renseigné mais variable absente
            create_provider({"provider": "monserveur", "model": "m", "base_url": "http://x/v1", "api_key_env": "MISSING"}, env={})

    def test_factory_reads_the_profile_settings_object(self):
        p = create_provider(LLMSettings(provider="gemini", model="m", api_key_env="K"), env={"K": "v"})
        self.assertEqual(p.cfg.api_key, "v")

    def test_missing_key_names_the_variable(self):
        with self.assertRaises(LLMAuthError) as cm:
            create_provider({"provider": "gemini", "model": "m", "api_key_env": "GOOGLE_API_KEY"}, env={})
        self.assertIn("GOOGLE_API_KEY", str(cm.exception))

    def test_unknown_provider_lists_the_options(self):
        with self.assertRaises(LLMError) as cm:
            create_provider({"provider": "inconnu"}, env={})
        self.assertIn("gemini", str(cm.exception))

    def test_complete_json_retries_once_on_invalid_json(self):
        n = {"i": 0}

        def handler(r):
            n["i"] += 1
            text = "pas du json" if n["i"] == 1 else '```json\n{"ok":true}\n```'
            return 200, {"choices": [{"message": {"content": text}}]}
        with MockServer(handler) as srv:
            p = OpenAICompatProvider(LLMConfig(provider="openai", model="m", base_url=srv.url + "/v1"), fast_http(), needs_key=False)
            data, _ = p.complete_json("s", "u")
            self.assertEqual(data, {"ok": True})
            self.assertEqual(n["i"], 2)


if __name__ == "__main__":
    unittest.main()
