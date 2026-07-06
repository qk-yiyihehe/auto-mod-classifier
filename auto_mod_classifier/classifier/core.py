from ..shared import *
from .offline_database import OfflineModDatabase
from .browser import ClassifierBrowserMixin
from .network import ClassifierNetworkMixin
from .text_utils import ClassifierTextMixin


class ClassifierCore(
    ClassifierBrowserMixin,
    ClassifierTextMixin,
    ClassifierNetworkMixin,
):
    def __init__(self, throttle_ms: int = 80, download_source: str = DOWNLOAD_SOURCE_SMART):
        self.throttle_ms = throttle_ms
        self.download_source = download_source
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
        self.use_curseforge_api: bool = True
        self.use_offline_database: bool = False
        self.offline_database = OfflineModDatabase()
        # 由界面层注入提示回调，核心层不直接依赖 Tk 弹窗
        self.browser_warning_callback: Optional[Callable[[Any], None]] = None
