from ..shared import *
from .browser import ClassifierBrowserMixin
from .network import ClassifierNetworkMixin
from .text_utils import ClassifierTextMixin


class ClassifierCore(
    ClassifierBrowserMixin,
    ClassifierTextMixin,
    ClassifierNetworkMixin,
):
    def __init__(self, throttle_ms: int = 80):
        self.throttle_ms = throttle_ms
        self.cache: Dict[str, object] = {}
        self.cache_lock = threading.Lock()
        self.inflight_requests: Dict[str, threading.Event] = {}
        self.request_lock = threading.Lock()
        self.next_request_at = 0.0
        self.mcmod_request_lock = threading.Lock()
        self.next_mcmod_request_at = 0.0
        self.modrinth_request_lock = threading.Lock()
        self.next_modrinth_request_at = 0.0

        # --- mcmod 反 CloudFlare：DrissionPage 浏览器 ---
        # 浏览器标签页池（3 标签页 = 3 并发，延迟初始化）
        self._browser_tabs: list = []
        self._browser_main_page = None
        self._browser_init_lock = threading.Lock()
        self._browser_tab_cond = threading.Condition()
        # 验证码全局锁：同一时刻只弹一个窗口
        self._captcha_lock = threading.Lock()
        self._captcha_done = threading.Event()
        self._captcha_done.set()
        # CurseForge 开关
        self.use_curseforge: bool = False
        # 由界面层注入提示回调，核心层不直接依赖 Tk 弹窗
        self.browser_warning_callback: Optional[Callable[[str], None]] = None
        # 调试日志
        self._dlog_file = Path(tempfile.gettempdir()) / "_mcmod_debug.log"
        try:
            self._dlog_file.write_text("", encoding="utf-8")
        except Exception:
            pass
