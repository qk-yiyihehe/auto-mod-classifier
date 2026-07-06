from ..shared import *


class ClassifierBrowserMixin:
    def _emit_browser_warning(self, payload: Any) -> None:
        callback = getattr(self, "browser_warning_callback", None)
        if not callable(callback):
            return
        try:
            callback(payload)
        except Exception:
            pass

    def _describe_browser_validation(self, url: str) -> dict[str, str]:
        lowered = str(url or "").lower()
        if "mcmod.cn" in lowered:
            return {
                "kind": "browser-validation",
                "title": "需要浏览器验证",
                "message": (
                    "接下来会临时弹出浏览器窗口，请手动完成 MC百科 的安全验证。\n\n"
                    "验证通过后程序会自动继续，无需重复点击开始。"
                ),
            }
        return {
            "kind": "browser-validation",
            "title": "需要浏览器验证",
            "message": (
                "接下来会临时弹出浏览器窗口，请手动完成 CurseForge / Cloudflare 的验证。\n\n"
                "验证通过后程序会自动继续，无需重复点击开始。"
            ),
        }

    def _is_captcha_page(self, html: str) -> bool:
        """检测 mc百科验证码 或 CloudFlare 拦截页面"""
        if not html:
            return False
        # mc百科验证码
        if "安全验证" in html and "captcha-box" in html:
            return True
        # CloudFlare Turnstile / 拦截（点击复选框型，含CurseForge）
        if len(html) < 12000 and ("Checking your browser" in html or "cf-browser-verification" in html or "Just a moment" in html or "cf-turnstile" in html or "challenge-platform" in html):
            return True
        # 页面太小，可能是 CF 拦截
        if len(html) < 3000:
            return True
        return False

    def _init_browser(self) -> bool:
        if not HAS_DRISSIONPAGE:
            return False
        with self._browser_init_lock:
            if self._browser_tabs:
                return True
            paths = [
                None,
                Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
            ]
            for bp in paths:
                try:
                    co = ChromiumOptions()
                    if bp is not None and bp.exists():
                        co.set_browser_path(str(bp))
                    co.set_argument("--disable-blink-features=AutomationControlled")
                    co.set_argument("--disable-features=TranslateUI")
                    co.set_argument("--no-first-run")
                    user_data = Path(tempfile.gettempdir()) / "_mcmod_browser_data"
                    user_data.mkdir(parents=True, exist_ok=True)
                    co.set_user_data_path(str(user_data))
                    co.set_argument("--window-position=-32000,-32000")
                    co.set_argument("--window-size=800,600")
                    main = ChromiumPage(co)
                    tabs = [(main, False)]
                    for _ in range(DEFAULT_MCMOD_WORKERS + DEFAULT_CF_WORKERS + 2 - 1):
                        try:
                            tabs.append((main.new_tab(), False))
                        except Exception:
                            break
                    self._browser_tabs = tabs
                    self._browser_main_page = main
                    atexit.register(self._cleanup_browser)
                    return True
                except Exception:
                    continue
            self._emit_browser_warning(
                "未找到 Chrome 或 Edge 浏览器，MC百科/CurseForge 查询将不可用。\n\n请安装 Chrome 或确保 Edge 在默认路径。"
            )
            return False

    def _cleanup_browser(self):
        with self._browser_init_lock:
            if self._browser_tabs:
                try:
                    self._browser_tabs[0][0].quit()
                except Exception:
                    pass
                self._browser_tabs = []
        # 清理浏览器缓存和临时文件
        for pattern in ["_mcmod_captcha*", "_mcmod_browser_data*", "_mcmod_cookies.json"]:
            for f in Path(tempfile.gettempdir()).glob(pattern):
                try:
                    if f.is_dir():
                        import shutil
                        shutil.rmtree(f, ignore_errors=True)
                    else:
                        f.unlink(missing_ok=True)
                except Exception:
                    pass

    def close_browser(self):
        """外部调用：主动关闭浏览器释放内存（2次筛选前）"""
        self._cleanup_browser()

    def _browser_show(self):
        try:
            self._browser_main_page.set.window.show()
            self._browser_main_page.set.window.location(100, 100)
            self._browser_main_page.set.window.size(800, 600)
        except Exception:
            pass

    def _browser_hide(self):
        try:
            self._browser_main_page.set.window.location(-32000, -32000)
        except Exception:
            pass

    def _browser_fetch(self, url: str) -> Optional[str]:
        """通过浏览器标签页池获取页面内容。"""
        if not self._browser_tabs and not self._init_browser():
            return None
        tab = None
        with self._browser_tab_cond:
            for i, (t, busy) in enumerate(self._browser_tabs):
                if not busy:
                    tab = t
                    self._browser_tabs[i] = (t, True)
                    break
        if tab is None:
            return None
        try:
            tab.get(url, timeout=15)
            # CurseForge React 渲染需要额外等待
            if "curseforge.com" in url:
                time.sleep(2)
            html = tab.html
            if self._is_captcha_page(html):
                if self._captcha_lock.acquire(blocking=False):
                    # 获得解决权：切到验证码标签页 → 弹出浏览器窗口
                    self._emit_browser_warning(self._describe_browser_validation(url))
                    try:
                        tab.set.activate()  # 切到验证码标签页
                    except Exception:
                        pass
                    self._browser_show()
                    self._captcha_done.clear()
                    for _ in range(120):  # 最多等 2 分钟
                        time.sleep(1)
                        try:
                            html = tab.html
                        except Exception:
                            continue
                        if not self._is_captcha_page(html) and len(html) > 200:
                            break
                    self._browser_hide()
                    self._captcha_done.set()
                    self._captcha_lock.release()
                else:
                    # 另一个标签页正在处理，等待它完成
                    self._captcha_done.wait(timeout=130)
                    # 重新加载本标签页
                    tab.get(url, timeout=15)
                    html = tab.html
            return html
        except Exception:
            return None
        finally:
            with self._browser_tab_cond:
                for i, (t, _) in enumerate(self._browser_tabs):
                    if t is tab:
                        self._browser_tabs[i] = (t, False)
                        break
                self._browser_tab_cond.notify()

    def _browser_export_cookies(self):
        if not self._browser_tabs or not self._mcmod_session:
            return
        try:
            for c in self._browser_tabs[0][0].cookies():
                self._mcmod_session.cookies.set(c.get("name", ""), c.get("value", ""))
            self._save_mcmod_cookies()
        except Exception:
            pass

