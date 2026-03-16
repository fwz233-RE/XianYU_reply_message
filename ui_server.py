import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from quick_replies import QuickReplyStore
from runtime_state import RuntimeState
from trigger_rules import KeywordReplyRuleStore, TriggerRuleStore


class SendMessageRequest(BaseModel):
    text: str


class AutoReplySettingRequest(BaseModel):
    enabled: bool


class QuickRepliesRequest(BaseModel):
    items: List[Dict[str, str]]


class TriggerRulesRequest(BaseModel):
    items: List[Dict[str, Any]]


class UiRuntimeController:
    def __init__(
        self,
        xianyu_live,
        runtime_state: RuntimeState,
        quick_reply_store: QuickReplyStore,
        trigger_rule_store: TriggerRuleStore,
        keyword_reply_rule_store: KeywordReplyRuleStore,
    ):
        self.live = xianyu_live
        self.runtime_state = runtime_state
        self.quick_reply_store = quick_reply_store
        self.trigger_rule_store = trigger_rule_store
        self.keyword_reply_rule_store = keyword_reply_rule_store
        self._thread: threading.Thread | None = None
        self._bridge = None

    def attach_runtime(self, xianyu_live, bridge=None) -> None:
        self.live = xianyu_live
        self._bridge = bridge

    def stop_bridge(self) -> None:
        if self._bridge:
            self._bridge.stop()
            self._bridge = None

    def start_runtime(self) -> None:
        if not self.live:
            raise RuntimeError("运行时尚未初始化")

        def _runner():
            asyncio.run(self.live.main())

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()

    def get_status(self) -> Dict[str, Any]:
        status = self.runtime_state.snapshot()
        if self.live:
            status["ws_url"] = self.live.base_url
        else:
            status["ws_url"] = "wss://wss-goofish.dingtalk.com/"
        return status

    def get_chats(self) -> List[Dict[str, Any]]:
        if not self.live:
            return []
        return self.live.context_manager.get_chat_list(limit=200)

    def get_chat_messages(self, chat_id: str) -> List[Dict[str, Any]]:
        if not self.live:
            return []
        return self.live.context_manager.get_messages_by_chat(chat_id, limit=300)

    def get_chat_state(self, chat_id: str) -> Dict[str, Any]:
        if not self.live:
            return {
                "chat_id": chat_id,
                "ai_state": "idle",
                "preview_text": "",
                "countdown_until": None,
                "cancelable": False,
                "trigger_state": "",
                "last_user_message_at": None,
                "pending_reason": "",
                "active_generation": 0,
                "ai_paused_until": None,
                "paused_reason": "",
            }
        return self.live.get_chat_state(chat_id)

    def send_message(self, chat_id: str, text: str) -> None:
        if not self.live:
            raise RuntimeError("运行时尚未就绪，请等待Cookie与连接初始化")
        if not text.strip():
            raise ValueError("消息不能为空")
        self.live.send_message_threadsafe(chat_id, text.strip())

    def cancel_chat_reply(self, chat_id: str) -> None:
        if not self.live:
            raise RuntimeError("运行时尚未就绪，请等待Cookie与连接初始化")
        self.live.cancel_reply_threadsafe(chat_id)

    def confirm_chat_reply(self, chat_id: str) -> None:
        if not self.live:
            raise RuntimeError("运行时尚未就绪，请等待Cookie与连接初始化")
        self.live.confirm_reply_threadsafe(chat_id)

    def resume_chat_ai(self, chat_id: str) -> None:
        if not self.live:
            return
        self.live.resume_chat_ai(chat_id)

    def set_auto_reply_enabled(self, enabled: bool) -> Dict[str, Any]:
        return self.runtime_state.update_status(auto_reply_enabled=bool(enabled))

    def list_quick_replies(self) -> List[Dict[str, str]]:
        return self.quick_reply_store.list()

    def save_quick_replies(self, items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        return self.quick_reply_store.replace(items)

    def list_trigger_rules(self) -> List[Dict[str, Any]]:
        return self.trigger_rule_store.list()

    def save_trigger_rules(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.trigger_rule_store.replace(items)

    def list_keyword_reply_rules(self) -> List[Dict[str, Any]]:
        return self.keyword_reply_rule_store.list()

    def save_keyword_reply_rules(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.keyword_reply_rule_store.replace(items)


def create_app(controller: UiRuntimeController) -> FastAPI:
    app = FastAPI(title="Xianyu IM UI", version="1.0.0")
    webui_dir = Path(__file__).parent / "webui"

    @app.get("/api/status")
    def get_status():
        return {"ok": True, "data": controller.get_status()}

    @app.get("/api/chats")
    def get_chats():
        return {"ok": True, "data": controller.get_chats()}

    @app.get("/api/chats/{chat_id}/messages")
    def get_chat_messages(chat_id: str):
        return {"ok": True, "data": controller.get_chat_messages(chat_id)}

    @app.get("/api/chats/{chat_id}/state")
    def get_chat_state(chat_id: str):
        return {"ok": True, "data": controller.get_chat_state(chat_id)}

    @app.post("/api/chats/{chat_id}/send")
    def send_chat_message(chat_id: str, req: SendMessageRequest):
        try:
            controller.send_message(chat_id, req.text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/cancel")
    def cancel_chat_reply(chat_id: str):
        try:
            controller.cancel_chat_reply(chat_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        controller.runtime_state.publish("reply_cancel_requested", {"chat_id": chat_id})
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/confirm")
    def confirm_chat_reply(chat_id: str):
        try:
            controller.confirm_chat_reply(chat_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        controller.runtime_state.publish("reply_confirm_requested", {"chat_id": chat_id})
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/resume-ai")
    def resume_chat_ai(chat_id: str):
        controller.resume_chat_ai(chat_id)
        return {"ok": True}

    @app.post("/api/settings/auto-reply")
    def update_auto_reply(req: AutoReplySettingRequest):
        status = controller.set_auto_reply_enabled(req.enabled)
        return {"ok": True, "data": status}

    @app.get("/api/quick-replies")
    def get_quick_replies():
        return {"ok": True, "data": controller.list_quick_replies()}

    @app.post("/api/quick-replies")
    def update_quick_replies(req: QuickRepliesRequest):
        saved = controller.save_quick_replies(req.items)
        controller.runtime_state.publish("quick_replies_updated", {"count": len(saved)})
        return {"ok": True, "data": saved}

    @app.get("/api/trigger-rules")
    def get_trigger_rules():
        return {"ok": True, "data": controller.list_trigger_rules()}

    @app.post("/api/trigger-rules")
    def update_trigger_rules(req: TriggerRulesRequest):
        saved = controller.save_trigger_rules(req.items)
        controller.runtime_state.publish("trigger_rules_updated", {"count": len(saved)})
        return {"ok": True, "data": saved}

    @app.get("/api/keyword-reply-rules")
    def get_keyword_reply_rules():
        return {"ok": True, "data": controller.list_keyword_reply_rules()}

    @app.post("/api/keyword-reply-rules")
    def update_keyword_reply_rules(req: TriggerRulesRequest):
        saved = controller.save_keyword_reply_rules(req.items)
        controller.runtime_state.publish("keyword_reply_rules_updated", {"count": len(saved)})
        return {"ok": True, "data": saved}

    @app.get("/api/events")
    async def events():
        async def event_generator():
            last_id = 0
            while True:
                events_list = controller.runtime_state.get_events_since(last_id)
                if events_list:
                    for e in events_list:
                        last_id = e["id"]
                        yield f"id: {e['id']}\n"
                        yield f"event: {e['type']}\n"
                        yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                else:
                    heartbeat = {"ts": int(time.time() * 1000)}
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/")
    def index():
        return FileResponse(webui_dir / "index.html")

    app.mount("/webui", StaticFiles(directory=webui_dir), name="webui")
    return app
