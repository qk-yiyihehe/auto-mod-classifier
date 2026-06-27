from ..shared import *


class ClassifierNetworkMixin:
    def throttle_request(self) -> None:
        interval = self.throttle_ms / 1000
        if interval <= 0:
            return
        with self.request_lock:
            now = time.monotonic()
            if now < self.next_request_at:
                time.sleep(self.next_request_at - now)
            self.next_request_at = time.monotonic() + interval

    def throttle_mcmod_request(self, interval_ms: int = 80) -> None:
        interval = interval_ms / 1000
        if interval <= 0:
            return
        with self.mcmod_request_lock:
            now = time.monotonic()
            if now < self.next_mcmod_request_at:
                time.sleep(self.next_mcmod_request_at - now)
            self.next_mcmod_request_at = time.monotonic() + interval

    def throttle_modrinth_request(self) -> None:
        with self.modrinth_request_lock:
            now = time.monotonic()
            if now < self.next_modrinth_request_at:
                time.sleep(self.next_modrinth_request_at - now)

    def update_modrinth_rate_limit(self, headers: Any, status_code: int = 200) -> None:
        limit = None
        remaining = None
        reset_seconds = None
        try:
            limit = int(headers.get("X-Ratelimit-Limit", "0") or 0)
        except Exception:
            limit = 0
        try:
            remaining = int(headers.get("X-Ratelimit-Remaining", "0") or 0)
        except Exception:
            remaining = 0
        try:
            reset_seconds = float(headers.get("X-Ratelimit-Reset", "0") or 0)
        except Exception:
            reset_seconds = 0.0

        fallback_interval = 0.22
        with self.modrinth_request_lock:
            now = time.monotonic()
            next_at = now + fallback_interval
            if status_code == 429:
                next_at = now + max(reset_seconds, 1.0)
            elif limit and remaining and reset_seconds > 0:
                next_at = now + max(reset_seconds / max(remaining, 1), fallback_interval)
            elif limit and remaining <= 0 and reset_seconds > 0:
                next_at = now + reset_seconds
            self.next_modrinth_request_at = max(self.next_modrinth_request_at, next_at)

    def is_mcmod_rate_limited(self, html: str) -> bool:
        if not html:
            return False
        return "搜索太频繁" in html or "稍后再试" in html or "鎼滅储澶" in html

    def mcmod_text_request(self, cache_key: str, url: str, max_attempts: int = 4) -> str:
        with self.cache_lock:
            cached = self.cache.get(cache_key)
            if isinstance(cached, str) and cached and not self.is_mcmod_rate_limited(cached) and not self._is_captcha_page(cached):
                return cached
            wait_event = self.inflight_requests.get(cache_key)
            owner = wait_event is None
            if owner:
                wait_event = threading.Event()
                self.inflight_requests[cache_key] = wait_event

        if not owner:
            assert wait_event is not None
            wait_event.wait()
            with self.cache_lock:
                cached = self.cache.get(cache_key)
            if isinstance(cached, str) and cached and not self.is_mcmod_rate_limited(cached) and not self._is_captcha_page(cached):
                return cached
            return ""

        last_html = ""
        try:
            for attempt in range(max_attempts):
                try:
                    self.throttle_request()
                    self.throttle_mcmod_request()
                    html = self.http_get_text(url) or ""
                except Exception:
                    html = ""
                last_html = html
                if html:
                    if self._is_captcha_page(html):
                        if not hasattr(self, '_mcmod_captcha_hits'):
                            self._mcmod_captcha_hits = {}
                        hits = self._mcmod_captcha_hits.get(cache_key, 0) + 1
                        self._mcmod_captcha_hits[cache_key] = hits
                        self._dlog(f"[{cache_key[:30]}] 验证码 #{hits} url={url[:80]}")
                        # 浏览器已经在 http_get_text 里尝试过了，这里只是记录
                        time.sleep(0.3 * (attempt + 1))
                        continue
                    elif not self.is_mcmod_rate_limited(html):
                        self._dlog(f"[{cache_key[:30]}] OK {len(html)} 字节")
                        with self.cache_lock:
                            self.cache[cache_key] = html
                        return html
                    else:
                        self._dlog(f"[{cache_key[:30]}] 限流，重试")
                time.sleep(0.2 * (attempt + 1))
            self._dlog(f"[{cache_key[:30]}] 放弃（{max_attempts}次未成功）")
            return ""
        finally:
            with self.cache_lock:
                event = self.inflight_requests.pop(cache_key, None)
                if event:
                    event.set()

    def get_cached_value(self, cache_key: str, loader: Callable[[], object]) -> object:
        owner = False
        wait_event: Optional[threading.Event] = None
        with self.cache_lock:
            if cache_key in self.cache:
                return self.cache[cache_key]
            wait_event = self.inflight_requests.get(cache_key)
            if wait_event is None:
                wait_event = threading.Event()
                self.inflight_requests[cache_key] = wait_event
                owner = True

        if not owner:
            assert wait_event is not None
            wait_event.wait()
            with self.cache_lock:
                return self.cache.get(cache_key)

        value: object = None
        try:
            value = loader()
        except Exception:
            value = None
        finally:
            with self.cache_lock:
                self.cache[cache_key] = value
                event = self.inflight_requests.pop(cache_key, None)
                if event:
                    event.set()
        return value

    def cached_json_request(self, cache_key: str, url: str, use_throttle: bool = True) -> Optional[dict]:
        value = self.get_cached_value(
            cache_key,
            lambda: (self.throttle_request(), self.http_get_json(url))[1] if use_throttle else self.http_get_json(url),
        )
        return value if isinstance(value, dict) else None

    def modrinth_json_request(self, cache_key: str, url: str, max_attempts: int = 3) -> Optional[dict]:
        def loader() -> Optional[dict]:
            last_payload: Optional[dict] = None
            for attempt in range(max_attempts):
                self.throttle_modrinth_request()
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                # Modrinth API 直连绕过系统代理（避免梯子串行化并发）
                _modrinth_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    with _modrinth_opener.open(req, timeout=20) as resp:
                        raw = resp.read()
                        charset = resp.headers.get_content_charset() or "utf-8"
                        payload = json.loads(raw.decode(charset, errors="ignore"))
                        self.update_modrinth_rate_limit(resp.headers, getattr(resp, "status", 200))
                        last_payload = payload if isinstance(payload, dict) else None
                        return last_payload
                except urllib.error.HTTPError as exc:
                    self.update_modrinth_rate_limit(exc.headers or {}, exc.code)
                    if exc.code == 429:
                        time.sleep(min(2.0 * (attempt + 1), 8.0))
                        continue
                    return None
                except Exception:
                    time.sleep(0.3 * (attempt + 1))
            return last_payload

        value = self.get_cached_value(cache_key, loader)
        return value if isinstance(value, dict) else None

    def cached_text_request(self, cache_key: str, url: str) -> str:
        value = self.get_cached_value(
            cache_key,
            lambda: (self.throttle_request(), self.http_get_text(url))[1],
        )
        return value if isinstance(value, str) else ""

    def http_get_json(self, url: str) -> Optional[dict]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return json.loads(raw.decode(charset, errors="ignore"))

    def http_get_text(self, url: str) -> Optional[str]:
        # mcmod.cn / curseforge.com：全部走真实浏览器
        if "mcmod.cn" in url or "curseforge.com" in url:
            if HAS_DRISSIONPAGE:
                return self._browser_fetch(url)
            return None
        # 其他请求保持原有 urllib
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="ignore")

