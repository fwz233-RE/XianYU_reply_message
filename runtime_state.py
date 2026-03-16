import itertools
import threading
import time
from typing import Any, Dict, List, Optional


class RuntimeState:
    """线程安全的运行时状态与事件总线。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._id_counter = itertools.count(1)
        self._events: List[Dict[str, Any]] = []
        self._max_events = 5000
        self._chat_states: Dict[str, Dict[str, Any]] = {}
        self._status: Dict[str, Any] = {
            "ws_connected": False,
            "auto_reply_enabled": True,
            "last_heartbeat_at": None,
            "last_token_refresh_at": None,
            "cookie_source": None,
            "last_cookie_update_at": None,
            "llm_last_ok_at": None,
            "llm_last_error": "",
            "cookie_waiting": False,
            "cookie_wait_reason": "",
            "bridge_online": False,
            "project_id": "",
            "account_hint": "",
            "instance_name": "",
            "browser_name": "",
            "env_file": "",
            "cookie_error": "",
            "last_event_at": int(time.time()),
        }

    def _default_chat_state(self, chat_id: str) -> Dict[str, Any]:
        return {
            "chat_id": chat_id,
            "ai_state": "idle",
            "generation_stage": "",
            "generation_started_at": None,
            "preview_text": "",
            "countdown_until": None,
            "cancelable": False,
            "trigger_state": "",
            "last_user_message_at": None,
            "pending_reason": "",
            "active_generation": 0,
            "ai_paused_until": None,
            "paused_reason": "",
            "updated_at": int(time.time()),
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def update_status(self, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            self._status.update(kwargs)
            self._status["last_event_at"] = int(time.time())
            status = dict(self._status)
        self.publish("status", status)
        return status

    def publish(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            event = {
                "id": next(self._id_counter),
                "ts": int(time.time() * 1000),
                "type": event_type,
                "payload": payload,
            }
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]
            return event

    def snapshot_chat_state(self, chat_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._chat_states.get(chat_id) or self._default_chat_state(chat_id)
            return dict(state)

    def update_chat_state(self, chat_id: str, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            state = dict(self._chat_states.get(chat_id) or self._default_chat_state(chat_id))
            state.update(kwargs)
            state["chat_id"] = chat_id
            state["updated_at"] = int(time.time())
            self._chat_states[chat_id] = state
            snapshot = dict(state)
        self.publish("chat_state_changed", snapshot)
        return snapshot

    def get_events_since(self, last_id: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    def get_last_event_id(self) -> int:
        with self._lock:
            if not self._events:
                return 0
            return self._events[-1]["id"]

    def append_message_event(self, message: Dict[str, Any]) -> None:
        self.publish("message", message)
