"""Small HTTP client for discovery requests (feeds, news RSS, paper API).

Every hop passes the same URL policy as page fetching (public addresses only,
manual redirects re-validated). Transports are injectable for offline tests.
"""
from __future__ import annotations

from ..fetch import MAX_REDIRECTS, FetchResponse, Fetcher, _absolutize

USER_AGENT = "Mozilla/5.0 (compatible; TireScout/0.2; feed reader for internal R&D news digest)"
MAX_FEED_BYTES = 5 * 1024 * 1024


class DiscoveryHttpError(Exception):
    pass


class DiscoveryHttp:
    def __init__(self, *, policy: Fetcher | None = None, get_transport=None, post_transport=None,
                 timeout_s: int = 20):
        self.policy = policy or Fetcher()
        self.timeout_s = timeout_s
        self._get = get_transport or self._httpx_get
        self._post = post_transport or self._httpx_post
        self.requests = 0

    def _httpx_get(self, url: str, headers: dict) -> FetchResponse:
        import httpx
        with httpx.Client(timeout=self.timeout_s, follow_redirects=False) as client:
            r = client.get(url, headers=headers)
        return FetchResponse(r.status_code, dict(r.headers), r.content, str(r.url))

    def _httpx_post(self, url: str, data: str, headers: dict) -> FetchResponse:
        import httpx
        with httpx.Client(timeout=self.timeout_s, follow_redirects=False) as client:
            r = client.post(url, content=data.encode("utf-8"), headers=headers)
        return FetchResponse(r.status_code, dict(r.headers), r.content, str(r.url))

    def get(self, url: str, *, accept: str = "*/*", headers: dict | None = None) -> FetchResponse:
        hdrs = {"User-Agent": USER_AGENT, "Accept": accept, **(headers or {})}
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            err = self.policy.validate_url(current)
            if err:
                raise DiscoveryHttpError(f"blocked: {err}")
            self.requests += 1
            try:
                resp = self._get(current, hdrs)
            except Exception as exc:
                raise DiscoveryHttpError(f"transport error: {exc}") from exc
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("location") or resp.headers.get("Location")
                if not location:
                    raise DiscoveryHttpError("redirect without location")
                current = _absolutize(current, location)
                continue
            if resp.status != 200:
                raise DiscoveryHttpError(f"HTTP {resp.status}")
            if len(resp.body) > MAX_FEED_BYTES:
                raise DiscoveryHttpError("response too large")
            resp.url = resp.url or current
            return resp
        raise DiscoveryHttpError(f"too many redirects (>{MAX_REDIRECTS})")

    def post(self, url: str, data: str, *, headers: dict | None = None) -> FetchResponse:
        err = self.policy.validate_url(url)
        if err:
            raise DiscoveryHttpError(f"blocked: {err}")
        self.requests += 1
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        try:
            resp = self._post(url, data, hdrs)
        except Exception as exc:
            raise DiscoveryHttpError(f"transport error: {exc}") from exc
        if resp.status != 200:
            raise DiscoveryHttpError(f"HTTP {resp.status}")
        return resp
