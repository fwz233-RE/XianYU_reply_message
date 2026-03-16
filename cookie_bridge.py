import hashlib
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

from dotenv import set_key
from loguru import logger

from utils.xianyu_utils import trans_cookies


REQUIRED_COOKIE_KEYS = ("unb", "_m_h5_tk", "cookie2", "cna")
COOKIE_DOMAIN_PRIORITY_BY_NAME = {
    "_m_h5_tk": (
        "h5api.m.goofish.com",
        "acs.m.goofish.com",
        "www.goofish.com",
        "goofish.com",
        "passport.goofish.com",
    ),
    "_m_h5_tk_enc": (
        "h5api.m.goofish.com",
        "acs.m.goofish.com",
        "www.goofish.com",
        "goofish.com",
        "passport.goofish.com",
    ),
    "XSRF-TOKEN": (
        "passport.goofish.com",
        "www.goofish.com",
        "goofish.com",
        "h5api.m.goofish.com",
    ),
    "cookie2": (
        "goofish.com",
        "www.goofish.com",
        "passport.goofish.com",
        "h5api.m.goofish.com",
    ),
    "unb": (
        "goofish.com",
        "www.goofish.com",
        "passport.goofish.com",
        "h5api.m.goofish.com",
    ),
    "cna": (
        "goofish.com",
        "www.goofish.com",
        "passport.goofish.com",
        "h5api.m.goofish.com",
    ),
}
DEFAULT_COOKIE_DOMAIN_PRIORITY = (
    "h5api.m.goofish.com",
    "acs.m.goofish.com",
    "www.goofish.com",
    "passport.goofish.com",
    "goofish.com",
)


def has_required_keys(cookies: Dict[str, str]) -> bool:
    return all(cookies.get(k) for k in REQUIRED_COOKIE_KEYS)


def normalize_domain(domain: str) -> str:
    return str(domain or "").strip().lstrip(".").lower()


def normalize_path(path: str) -> str:
    value = str(path or "").strip()
    return value or "/"


def cookie_priority(item: Dict[str, Any]) -> tuple:
    name = str(item.get("name", "")).strip()
    domain = normalize_domain(item.get("domain", ""))
    path = normalize_path(item.get("path", "/"))
    preferred_domains = COOKIE_DOMAIN_PRIORITY_BY_NAME.get(name, DEFAULT_COOKIE_DOMAIN_PRIORITY)
    try:
        domain_rank = preferred_domains.index(domain)
    except ValueError:
        domain_rank = len(preferred_domains)
    host_only = 0 if item.get("hostOnly") else 1
    root_path = 0 if path == "/" else 1
    expiry = 0 if item.get("expirationDate") else 1
    return (domain_rank, host_only, root_path, len(path), expiry)


def select_cookie_map(cookie_str: str = "", cookie_items: Optional[list[Dict[str, Any]]] = None) -> Dict[str, str]:
    selected: Dict[str, Dict[str, Any]] = {}
    if cookie_items:
        for raw in cookie_items:
            name = str(raw.get("name", "")).strip()
            value = str(raw.get("value", "")).strip()
            if not name or not value:
                continue
            candidate = {
                "name": name,
                "value": value,
                "domain": raw.get("domain", ""),
                "path": raw.get("path", "/"),
                "hostOnly": bool(raw.get("hostOnly", False)),
                "expirationDate": raw.get("expirationDate"),
            }
            current = selected.get(name)
            if current is None or cookie_priority(candidate) < cookie_priority(current):
                selected[name] = candidate
        if selected:
            return {name: item["value"] for name, item in selected.items()}
    return trans_cookies(cookie_str)


def normalize_cookie_string(cookie_str: str) -> str:
    cookies = select_cookie_map(cookie_str)
    return "; ".join(f"{k}={cookies[k]}" for k in sorted(cookies.keys()))


def normalize_cookie_payload(cookie_str: str = "", cookie_items: Optional[list[Dict[str, Any]]] = None) -> str:
    cookies = select_cookie_map(cookie_str=cookie_str, cookie_items=cookie_items)
    return "; ".join(f"{k}={cookies[k]}" for k in sorted(cookies.keys()))


def cookie_hash(cookie_str: str = "", cookie_items: Optional[list[Dict[str, Any]]] = None) -> str:
    normalized = normalize_cookie_payload(cookie_str=cookie_str, cookie_items=cookie_items)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def mask_cookie_for_log(cookie_str: str) -> str:
    cookies = trans_cookies(cookie_str)
    visible = []
    for k in REQUIRED_COOKIE_KEYS:
        v = cookies.get(k, "")
        if not v:
            visible.append(f"{k}=<missing>")
        elif len(v) <= 8:
            visible.append(f"{k}=***")
        else:
            visible.append(f"{k}={v[:4]}...{v[-4:]}")
    return ", ".join(visible)


@dataclass
class CookieWaitRequest:
    baseline_hash: Optional[str]
    require_change: bool
    event: threading.Event
    result_cookie: Optional[str] = None


class CookieBridgeServer:
    """本地Cookie桥接服务：接收浏览器插件推送并唤醒Python主流程。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18765,
        token: str = "",
        env_path: str = ".env",
        project_id: str = "",
        account_hint: str = "",
    ):
        self.host = host
        self.port = int(port)
        self.token = token
        self.env_path = env_path
        self.project_id = str(project_id or "").strip()
        self.account_hint = str(account_hint or "").strip()

        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._wait_request: Optional[CookieWaitRequest] = None

        self._startup_first_hash: Optional[str] = None
        self._startup_change_seen = False
        self._startup_same_count: int = 0
        self._latest_cookie: Optional[str] = None
        self._latest_hash: Optional[str] = None
        self._listeners: list[Callable[[str], None]] = []

    def add_cookie_listener(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    @property
    def latest_cookie(self) -> Optional[str]:
        return self._latest_cookie

    @property
    def latest_hash(self) -> Optional[str]:
        return self._latest_hash

    def start(self) -> None:
        if self._httpd:
            return

        server = self

        class Handler(BaseHTTPRequestHandler):
            def _json_response(self, code: int, payload: Dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> Optional[Dict]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    return None

            def do_GET(self):  # noqa: N802
                if self.path != "/health":
                    self._json_response(404, {"ok": False, "error": "not_found"})
                    return
                self._json_response(
                    200,
                    {
                        "ok": True,
                        "requiredKeys": list(REQUIRED_COOKIE_KEYS),
                        "hasToken": bool(server.token),
                        "projectId": server.project_id,
                        "accountHint": server.account_hint,
                    },
                )

            def do_POST(self):  # noqa: N802
                if self.path != "/cookie":
                    self._json_response(404, {"ok": False, "error": "not_found"})
                    return

                payload = self._read_json()
                if payload is None:
                    self._json_response(400, {"ok": False, "error": "invalid_json"})
                    return

                token = str(payload.get("token", "")).strip()
                if server.token and token != server.token:
                    self._json_response(401, {"ok": False, "error": "unauthorized"})
                    return

                cookie_str = str(payload.get("cookies", "")).strip()
                cookie_items = payload.get("cookieItems")
                if not cookie_str and not cookie_items:
                    self._json_response(400, {"ok": False, "error": "empty_cookies"})
                    return

                stage = str(payload.get("stage", "auto")).strip().lower()
                source = str(payload.get("source", "extension")).strip()
                project_id = str(payload.get("projectId", "")).strip()
                account_hint = str(payload.get("accountHint", "")).strip()
                accepted, reason = server.accept_cookie(
                    cookie_str,
                    stage=stage,
                    source=source,
                    project_id=project_id,
                    account_hint=account_hint,
                    cookie_items=cookie_items if isinstance(cookie_items, list) else None,
                )
                code = 200 if accepted else 202
                self._json_response(code, {"ok": accepted, "accepted": accepted, "reason": reason})

            def log_message(self, format, *args):  # noqa: A003
                return

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"Cookie bridge listening at http://{self.host}:{self.port}")

    def stop(self) -> None:
        if not self._httpd:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
        logger.info("Cookie bridge stopped")

    def accept_cookie(
        self,
        cookie_str: str,
        stage: str = "auto",
        source: str = "extension",
        project_id: str = "",
        account_hint: str = "",
        cookie_items: Optional[list[Dict[str, Any]]] = None,
    ) -> tuple[bool, str]:
        if self.project_id and project_id and self.project_id != project_id:
            logger.warning(f"忽略来自其他项目的Cookie推送: {project_id}")
            return False, "project_id_mismatch"

        normalized = normalize_cookie_payload(cookie_str=cookie_str, cookie_items=cookie_items)
        cookies = trans_cookies(normalized)
        if not has_required_keys(cookies):
            return False, "missing_required_keys"
        if self.account_hint and account_hint and self.account_hint != account_hint:
            logger.warning(f"忽略来自其他账号的Cookie推送: {account_hint}")
            return False, "account_hint_mismatch"

        c_hash = cookie_hash(cookie_str=normalized)

        with self._lock:
            if self._latest_hash and c_hash == self._latest_hash and self._startup_change_seen:
                if self._wait_request and self._wait_request.require_change:
                    return False, "waiting_cookie_change_from_baseline"
                return False, "duplicate_cookie"

            self._latest_cookie = normalized
            self._latest_hash = c_hash

            # 启动流程双阶段：首次看到仅记基线，后续变化才允许放行
            if not self._startup_first_hash:
                self._startup_first_hash = c_hash
                self._startup_change_seen = False
                self._startup_same_count = 1
                logger.info(f"收到首份Cookie，等待变化后启用（{mask_cookie_for_log(normalized)}）")
                return False, "startup_first_snapshot_recorded"

            if not self._startup_change_seen:
                if c_hash == self._startup_first_hash and stage != "final":
                    self._startup_same_count += 1
                    # Edge等稳定会话Cookie不会主动变化，连续收到相同Cookie超过阈值后直接放行
                    if self._startup_same_count < 8:
                        return False, "waiting_cookie_change_after_first_snapshot"
                    logger.info(
                        f"Cookie持续无变化（已收到{self._startup_same_count}次），"
                        "判定为稳定会话，直接放行"
                    )
                self._startup_change_seen = True
                self._startup_same_count = 0

            self._persist_cookie(normalized)
            logger.success(f"收到可用Cookie[{source}]，已写入.env（{mask_cookie_for_log(normalized)}）")

            # 唤醒等待中的请求
            if self._wait_request:
                req = self._wait_request
                if req.require_change and req.baseline_hash and c_hash == req.baseline_hash:
                    return False, "waiting_cookie_change_from_baseline"
                req.result_cookie = normalized
                req.event.set()
                self._wait_request = None

        for listener in self._listeners:
            try:
                listener(normalized)
            except Exception as e:
                logger.warning(f"Cookie listener执行失败: {e}")

        return True, "cookie_accepted"

    def wait_for_cookie(
        self,
        timeout_seconds: int,
        baseline_cookie: Optional[str] = None,
        require_change: bool = False,
    ) -> Optional[str]:
        baseline_hash = cookie_hash(baseline_cookie) if baseline_cookie else None

        with self._lock:
            if self._latest_cookie:
                latest_hash = self._latest_hash
                if not (require_change and baseline_hash and latest_hash == baseline_hash):
                    return self._latest_cookie

            event = threading.Event()
            self._wait_request = CookieWaitRequest(
                baseline_hash=baseline_hash,
                require_change=require_change,
                event=event,
            )

        ok = event.wait(timeout=max(1, int(timeout_seconds)))
        if not ok:
            with self._lock:
                if self._wait_request and self._wait_request.event is event:
                    self._wait_request = None
            return None

        with self._lock:
            # 请求已被accept_cookie清空，结果在event对象里不可直接取，回退latest_cookie即可
            return self._latest_cookie

    def reset_startup_phase(self) -> None:
        with self._lock:
            self._startup_first_hash = None
            self._startup_change_seen = False
            self._startup_same_count = 0
        logger.info("Cookie bridge已重置为“首包+变化”等待阶段")

    def _persist_cookie(self, cookie_str: str) -> None:
        try:
            set_key(self.env_path, "COOKIES_STR", cookie_str)
        except Exception as e:
            logger.warning(f"写入.env失败: {e}")
