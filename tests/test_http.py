import unittest
import urllib.error

from .helpers import make_http
from rjh.http import HttpClient, HttpError, RateLimiter, safe_url


class Retry(unittest.TestCase):
    def test_429_honours_retry_after_then_succeeds(self):
        n = {"i": 0}
        sleeps = []

        def resp(call):
            n["i"] += 1
            return (429, {"error": "slow down"}, {"Retry-After": "2"}) if n["i"] == 1 else (200, {"ok": True})
        http, t = make_http(resp)
        http.sleep = sleeps.append
        self.assertEqual(http.get_json("https://api.example.com/x"), {"ok": True})
        self.assertEqual(len(t.calls), 2)
        self.assertEqual(sleeps, [2.0])

    def test_404_is_not_retried(self):
        http, t = make_http(lambda c: (404, {"error": "nope"}))
        with self.assertRaises(HttpError) as cm:
            http.get_json("https://api.example.com/x")
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(len(t.calls), 1)

    def test_gives_up_after_max_retries(self):
        http, t = make_http(lambda c: (503, {"error": "down"}), max_retries=3)
        with self.assertRaises(HttpError) as cm:
            http.get_json("https://api.example.com/x")
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(len(t.calls), 4)

    def test_connection_errors_are_retried_then_reported(self):
        calls = []

        def boom(*a):
            calls.append(1)
            raise urllib.error.URLError("refused")
        http = HttpClient(transport=boom, limiter=RateLimiter(0), sleep=lambda s: None, max_retries=2)
        with self.assertRaises(HttpError) as cm:
            http.get_json("https://api.example.com/x")
        self.assertEqual(cm.exception.status, 0)
        self.assertEqual(len(calls), 3)


class Safety(unittest.TestCase):
    def test_secrets_in_query_string_never_reach_error_messages(self):
        http, _ = make_http(lambda c: (500, {"error": "x"}), max_retries=0)
        with self.assertRaises(HttpError) as cm:
            http.get_json("https://api.example.com/search", params={"app_key": "SUPER_SECRET_KEY", "what": "x"})
        self.assertNotIn("SUPER_SECRET_KEY", str(cm.exception))
        self.assertNotIn("SUPER_SECRET_KEY", cm.exception.url)

    def test_safe_url_strips_query_and_fragment(self):
        self.assertEqual(safe_url("https://a.com/p?k=1#f"), "https://a.com/p")

    def test_non_http_schemes_are_refused(self):
        http, _ = make_http(lambda c: {})
        for url in ("file:///etc/passwd", "ftp://x.com/a", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                http.get_json(url)

    def test_none_params_are_dropped_and_json_body_sent(self):
        http, t = make_http(lambda c: {"ok": 1})
        http.post_json("https://a.com/p", params={"a": 1, "b": None}, json_body={"x": 2}, headers={"X-K": "v"})
        c = t.calls[0]
        self.assertTrue(c["url"].endswith("?a=1"))
        self.assertEqual(c["body"], {"x": 2})
        self.assertEqual(c["headers"]["Content-Type"], "application/json")
        self.assertEqual(c["headers"]["X-K"], "v")
        self.assertIn("rjh", c["headers"]["User-Agent"])


class Limiter(unittest.TestCase):
    def test_spacing_with_a_frozen_clock(self):
        sleeps = []
        lim = RateLimiter(1.0, clock=lambda: 0.0, sleep=sleeps.append)
        for _ in range(3):
            lim.wait()
        self.assertEqual(sleeps, [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
