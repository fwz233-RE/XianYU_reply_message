import base64
import json
import asyncio
import time
import os
import websockets
from loguru import logger
from dotenv import load_dotenv, set_key
from XianyuApis import XianyuApis
import sys
from typing import Optional


from utils.xianyu_utils import generate_mid, generate_uuid, trans_cookies, generate_device_id, decrypt
from XianyuAgent import XianyuReplyBot
from context_manager import ChatContextManager
from cookie_bridge import CookieBridgeServer, cookie_hash
from project_paths import get_browser_name, get_env_example_paths, get_env_file_path, get_instance_name
from runtime_state import RuntimeState
from trigger_rules import KeywordReplyRuleStore, TriggerRuleStore


bot = None


class XianyuLive:
    def __init__(
        self,
        cookies_str,
        cookie_refresh_callback=None,
        runtime_state: Optional[RuntimeState] = None,
        trigger_rule_store: Optional[TriggerRuleStore] = None,
        keyword_reply_rule_store: Optional[KeywordReplyRuleStore] = None,
    ):
        self.xianyu = XianyuApis()
        self.base_url = 'wss://wss-goofish.dingtalk.com/'
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        if not self.xianyu.set_cookies_from_string(cookies_str, persist_env=False):
            raise ValueError(
                "COOKIES_STR 缺少必要字段 unb。"
                "请从闲鱼网页版完整复制 Cookie（包含 unb、_m_h5_tk、cookie2、cna 等字段）后重试。"
            )
        self.myid = self.cookies.get('unb')
        if not self.myid:
            raise ValueError(
                "COOKIES_STR 缺少必要字段 unb。"
                "请从闲鱼网页版完整复制 Cookie（包含 unb、_m_h5_tk、cookie2、cna 等字段）后重试。"
            )
        self.device_id = generate_device_id(self.myid)
        self.context_manager = ChatContextManager()
        self.trigger_rule_store = trigger_rule_store or TriggerRuleStore()
        self.keyword_reply_rule_store = keyword_reply_rule_store or KeywordReplyRuleStore()

        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", "15"))
        self.heartbeat_timeout = int(os.getenv("HEARTBEAT_TIMEOUT", "5"))
        self.last_heartbeat_time = 0
        self.last_heartbeat_response = 0
        self.heartbeat_task = None
        self.ws = None

        self.token_refresh_interval = int(os.getenv("TOKEN_REFRESH_INTERVAL", "3600"))
        self.token_retry_interval = int(os.getenv("TOKEN_RETRY_INTERVAL", "300"))
        self.last_token_refresh_time = 0
        self.current_token = None
        self.token_refresh_task = None
        self.connection_restart_flag = False

        self.message_expire_time = int(os.getenv("MESSAGE_EXPIRE_TIME", "300000"))
        self.reply_preview_seconds = int(os.getenv("REPLY_PREVIEW_SECONDS", "5"))
        self.runtime_state = runtime_state
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.cookie_refresh_event: Optional[asyncio.Event] = None
        self.chat_peer_map = {}
        self.chat_item_map = {}
        self.chat_worker_tasks = {}
        self.chat_generation_versions = {}
        self.chat_generation_enabled = {}
        self.xianyu.set_cookie_updated_callback(self._on_cookie_updated)
        self.xianyu.set_status_callback(self._on_api_status)
        if cookie_refresh_callback:
            self.xianyu.set_cookie_refresh_callback(cookie_refresh_callback)

    def _on_cookie_updated(self, cookie_str: str):
        cookies = trans_cookies(cookie_str)
        if not cookies.get('unb'):
            logger.warning("收到无效Cookie更新（缺少unb），忽略")
            return

        old_myid = self.myid
        self.cookies_str = cookie_str
        self.cookies = cookies
        self.myid = cookies.get('unb')
        if self.myid != old_myid:
            self.device_id = generate_device_id(self.myid)
            logger.warning("检测到Cookie账号变化，已更新设备ID")
        self.current_token = None
        self.last_token_refresh_time = 0
        logger.info("运行时Cookie已更新")
        if self.runtime_state:
            self.runtime_state.update_status(
                last_cookie_update_at=int(time.time()),
                cookie_error="",
                cookie_waiting=False,
                cookie_wait_reason="",
            )
        if self.event_loop:
            if self.cookie_refresh_event:
                try:
                    self.event_loop.call_soon_threadsafe(self.cookie_refresh_event.set)
                except Exception:
                    pass
            self.connection_restart_flag = True
            if self.ws:
                try:
                    asyncio.run_coroutine_threadsafe(self.ws.close(), self.event_loop)
                except Exception as e:
                    logger.warning(f"验证完成后触发重连失败: {e}")

    def _on_api_status(self, event_name: str, payload: dict):
        if not self.runtime_state:
            return
        if event_name == "token_refresh_success":
            self.runtime_state.update_status(last_token_refresh_at=int(time.time()), cookie_error="")
        elif event_name == "risk_control_waiting_cookie":
            wait_reason = str((payload or {}).get("reason") or "risk_control")
            self.current_token = None
            self.last_token_refresh_time = 0
            self.runtime_state.update_status(cookie_waiting=True, cookie_wait_reason=wait_reason, cookie_error="")
        elif event_name == "risk_control_cookie_applied":
            self.runtime_state.update_status(cookie_waiting=False, cookie_wait_reason="", last_cookie_update_at=int(time.time()), cookie_error="")

    def _ensure_generation(self, chat_id: str) -> int:
        version = self.chat_generation_versions.get(chat_id, 0) + 1
        self.chat_generation_versions[chat_id] = version
        return version

    def _current_generation(self, chat_id: str) -> int:
        return self.chat_generation_versions.get(chat_id, 0)

    def _is_generation_stale(self, chat_id: str, version: int) -> bool:
        return self._current_generation(chat_id) != version

    def _update_chat_state(self, chat_id: str, **kwargs):
        if not self.runtime_state:
            return
        self.runtime_state.update_chat_state(chat_id, **kwargs)

    def get_chat_state(self, chat_id: str):
        if not self.runtime_state:
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
            }
        return self.runtime_state.snapshot_chat_state(chat_id)

    def _is_auto_reply_enabled(self) -> bool:
        if not self.runtime_state:
            return True
        return bool(self.runtime_state.snapshot().get("auto_reply_enabled", True))

    _AFTER_SALES_KEYWORDS = [
        '售后', '坏了', '损坏', '维修', '退款', '退货', '换货', '质量问题',
        '有问题', '不能用', '不好用', '不工作', '不正常', '故障', '发错',
        '漏发', '缺件', '投诉', '保修', '坏的', '损了',
    ]
    _AFTER_SALES_PATTERNS = [r'(退|换).{0,4}(货|款)', r'坏.{0,3}(了|的)']

    def _detect_after_sales(self, user_msg: str) -> bool:
        import re as _re
        text = _re.sub(r'[^\w\u4e00-\u9fa5]', '', user_msg)
        if any(kw in text for kw in self._AFTER_SALES_KEYWORDS):
            return True
        return any(_re.search(p, text) for p in self._AFTER_SALES_PATTERNS)

    def _is_chat_ai_paused(self, chat_id: str) -> bool:
        """检查指定对话AI是否处于暂停状态"""
        state = self.get_chat_state(chat_id)
        paused_until = state.get("ai_paused_until")
        return bool(paused_until and paused_until > time.time())

    def _set_chat_ai_pause(self, chat_id: str, duration_seconds: int = 1800, reason: str = "manual_reply"):
        """暂停指定对话的AI回复，默认30分钟"""
        paused_until = int(time.time()) + duration_seconds
        self._update_chat_state(chat_id, ai_paused_until=paused_until, paused_reason=reason)
        if self.runtime_state:
            self.runtime_state.publish("ai_paused", {
                "chat_id": chat_id,
                "paused_until": paused_until,
                "reason": reason,
            })
        logger.info(f"会话 {chat_id} AI已暂停 {duration_seconds // 60} 分钟（原因: {reason}）")

    def resume_chat_ai(self, chat_id: str):
        """取消指定对话的AI暂停"""
        self._update_chat_state(chat_id, ai_paused_until=None, paused_reason="")
        logger.info(f"会话 {chat_id} AI暂停已手动取消")

    async def refresh_token(self):
        try:
            logger.info("开始刷新token...")
            token_result = await asyncio.to_thread(self.xianyu.get_token, self.device_id)
            if isinstance(token_result, dict) and "WAITING_FOR_COOKIE_REFRESH" in str(token_result.get("ret", [])):
                logger.info("正在等待新的Cookie完成风控验证后继续获取Token")
                return "waiting_cookie"
            if 'data' in token_result and 'accessToken' in token_result['data']:
                new_token = token_result['data']['accessToken']
                self.current_token = new_token
                self.last_token_refresh_time = time.time()
                logger.info("Token刷新成功")
                if self.runtime_state:
                    self.runtime_state.update_status(last_token_refresh_at=int(self.last_token_refresh_time))
                return new_token
            logger.error(f"Token刷新失败: {token_result}")
            return None
        except Exception as e:
            logger.error(f"Token刷新异常: {str(e)}")
            return None

    async def _wait_for_cookie_refresh_signal(self, timeout_seconds: int = 600):
        if not self.cookie_refresh_event:
            await asyncio.sleep(2)
            return
        if self.cookie_refresh_event.is_set():
            self.cookie_refresh_event.clear()
            return
        try:
            await asyncio.wait_for(self.cookie_refresh_event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("等待拖动验证完成超时，继续检查Cookie状态")
        finally:
            if self.cookie_refresh_event.is_set():
                self.cookie_refresh_event.clear()

    async def token_refresh_loop(self):
        while True:
            try:
                current_time = time.time()
                if current_time - self.last_token_refresh_time >= self.token_refresh_interval:
                    logger.info("Token即将过期，准备刷新...")
                    new_token = await self.refresh_token()
                    if new_token:
                        if new_token == "waiting_cookie":
                            logger.info("等待拖动验证完成后的新Cookie，暂停Token重试")
                            await self._wait_for_cookie_refresh_signal()
                            continue
                        logger.info("Token刷新成功，准备重新建立连接...")
                        self.connection_restart_flag = True
                        if self.ws:
                            await self.ws.close()
                        break
                    logger.error("Token刷新失败，将在{}分钟后重试".format(self.token_retry_interval // 60))
                    await asyncio.sleep(self.token_retry_interval)
                    continue
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Token刷新循环出错: {e}")
                await asyncio.sleep(60)

    async def send_msg(self, ws, cid, toid, text):
        text = {
            "contentType": 1,
            "text": {
                "text": text
            }
        }
        text_base64 = str(base64.b64encode(json.dumps(text).encode('utf-8')), 'utf-8')
        msg = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {
                "mid": generate_mid()
            },
            "body": [
                {
                    "uuid": generate_uuid(),
                    "cid": f"{cid}@goofish",
                    "conversationType": 1,
                    "content": {
                        "contentType": 101,
                        "custom": {
                            "type": 1,
                            "data": text_base64
                        }
                    },
                    "redPointPolicy": 0,
                    "extension": {
                        "extJson": "{}"
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    },
                    "mtags": {},
                    "msgReadStatusSetting": 1
                },
                {
                    "actualReceivers": [
                        f"{toid}@goofish",
                        f"{self.myid}@goofish"
                    ]
                }
            ]
        }
        await ws.send(json.dumps(msg))

    async def init(self, ws):
        if not self.current_token or (time.time() - self.last_token_refresh_time) >= self.token_refresh_interval:
            while not self.current_token:
                logger.info("获取初始token...")
                refresh_result = await self.refresh_token()
                if refresh_result == "waiting_cookie":
                    logger.info("当前正在等待拖动验证完成，暂停初始化重试")
                    await self._wait_for_cookie_refresh_signal()
                    continue
                if self.current_token:
                    break
                logger.error("无法获取有效token，初始化失败")
                raise Exception("Token获取失败")

        if not self.current_token:
            logger.error("无法获取有效token，初始化失败")
            raise Exception("Token获取失败")

        msg = {
            "lwp": "/reg",
            "headers": {
                "cache-header": "app-key token ua wv",
                "app-key": "444e9908a51d1cb236a27862abc769c9",
                "token": self.current_token,
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 DingTalk(2.1.5) OS(Windows/10) Browser(Chrome/133.0.0.0) DingWeb/2.1.5 IMPaaS DingWeb/2.1.5",
                "dt": "j",
                "wv": "im:3,au:3,sy:6",
                "sync": "0,0;0;0;",
                "did": self.device_id,
                "mid": generate_mid()
            }
        }
        await ws.send(json.dumps(msg))
        await asyncio.sleep(1)
        msg = {"lwp": "/r/SyncStatus/ackDiff", "headers": {"mid": "5701741704675979 0"}, "body": [
            {"pipeline": "sync", "tooLong2Tag": "PNM,1", "channel": "sync", "topic": "sync", "highPts": 0,
             "pts": int(time.time() * 1000) * 1000, "seq": 0, "timestamp": int(time.time() * 1000)}]}
        await ws.send(json.dumps(msg))
        logger.info('连接注册完成')

    def is_chat_message(self, message):
        try:
            return (
                isinstance(message, dict)
                and "1" in message
                and isinstance(message["1"], dict)
                and "10" in message["1"]
                and isinstance(message["1"]["10"], dict)
                and "reminderContent" in message["1"]["10"]
            )
        except Exception:
            return False

    def is_sync_package(self, message_data):
        try:
            return (
                isinstance(message_data, dict)
                and "body" in message_data
                and "syncPushPackage" in message_data["body"]
                and "data" in message_data["body"]["syncPushPackage"]
                and len(message_data["body"]["syncPushPackage"]["data"]) > 0
            )
        except Exception:
            return False

    def is_typing_status(self, message):
        try:
            return (
                isinstance(message, dict)
                and "1" in message
                and isinstance(message["1"], list)
                and len(message["1"]) > 0
                and isinstance(message["1"][0], dict)
                and "1" in message["1"][0]
                and isinstance(message["1"][0]["1"], str)
                and "@goofish" in message["1"][0]["1"]
            )
        except Exception:
            return False

    def is_system_message(self, message):
        try:
            return (
                isinstance(message, dict)
                and "3" in message
                and isinstance(message["3"], dict)
                and "needPush" in message["3"]
                and message["3"]["needPush"] == "false"
            )
        except Exception:
            return False

    def is_bracket_system_message(self, message):
        try:
            if not message or not isinstance(message, str):
                return False
            clean_message = message.strip()
            if clean_message.startswith('[') and clean_message.endswith(']'):
                logger.debug(f"检测到系统消息: {clean_message}")
                return True
            return False
        except Exception as e:
            logger.error(f"检查系统消息失败: {e}")
            return False

    async def _cancel_chat_processing(self, chat_id: str, reason: str = "cancelled"):
        version = self._ensure_generation(chat_id)
        self.chat_generation_enabled[chat_id] = False
        self._update_chat_state(
            chat_id,
            ai_state="cancelled",
            generation_stage="已停止当前生成",
            generation_started_at=None,
            preview_text="",
            countdown_until=None,
            cancelable=False,
            trigger_state="",
            pending_reason=reason,
            active_generation=version,
        )
        await asyncio.sleep(0)
        return version

    async def manual_send_message(self, chat_id, text):
        toid = self.chat_peer_map.get(chat_id) or await asyncio.to_thread(self.context_manager.get_last_user_id_by_chat, chat_id)
        if not toid:
            raise ValueError(f"会话 {chat_id} 暂无可用目标用户ID")
        if not self.ws:
            raise RuntimeError("当前WebSocket未连接")

        await self._cancel_chat_processing(chat_id, reason="manual_send")
        item_id = self.chat_item_map.get(chat_id) or await asyncio.to_thread(self.context_manager.get_last_item_id_by_chat, chat_id) or ""
        await self.send_msg(self.ws, chat_id, toid, text)
        await asyncio.to_thread(self.context_manager.add_message_by_chat, chat_id, self.myid, item_id, "assistant", text)
        if self.runtime_state:
            self.runtime_state.append_message_event(
                {
                    "chat_id": chat_id,
                    "role": "assistant",
                    "content": text,
                    "user_id": self.myid,
                    "item_id": item_id,
                    "timestamp": int(time.time() * 1000),
                }
            )
        self._set_chat_ai_pause(chat_id, duration_seconds=1800, reason="manual_reply")
        self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, preview_text="", countdown_until=None, cancelable=False, pending_reason="manual_sent")

    def send_message_threadsafe(self, chat_id: str, text: str, timeout: int = 8):
        if not self.event_loop:
            raise RuntimeError("运行循环尚未启动")
        fut = asyncio.run_coroutine_threadsafe(self.manual_send_message(chat_id, text), self.event_loop)
        return fut.result(timeout=timeout)

    def cancel_reply_threadsafe(self, chat_id: str, timeout: int = 8):
        if not self.event_loop:
            raise RuntimeError("运行循环尚未启动")
        fut = asyncio.run_coroutine_threadsafe(self._cancel_chat_processing(chat_id, reason="ui_cancel"), self.event_loop)
        return fut.result(timeout=timeout)

    async def confirm_reply(self, chat_id: str):
        state = self.get_chat_state(chat_id)
        preview_text = str(state.get("preview_text", "") or "").strip()
        if state.get("ai_state") != "preview_countdown" or not preview_text:
            raise ValueError("当前没有可确认发送的预发送回复")

        toid = self.chat_peer_map.get(chat_id) or await asyncio.to_thread(self.context_manager.get_last_user_id_by_chat, chat_id)
        item_id = self.chat_item_map.get(chat_id) or await asyncio.to_thread(self.context_manager.get_last_item_id_by_chat, chat_id) or ""
        if not toid:
            raise ValueError(f"会话 {chat_id} 暂无可用目标用户ID")
        if not self.ws:
            raise RuntimeError("当前WebSocket未连接")

        version = self._ensure_generation(chat_id)
        self.chat_generation_enabled[chat_id] = False
        await self.send_msg(self.ws, chat_id, toid, preview_text)
        await asyncio.to_thread(self.context_manager.add_message_by_chat, chat_id, self.myid, item_id, "assistant", preview_text)
        if self.runtime_state:
            self.runtime_state.append_message_event(
                {
                    "chat_id": chat_id,
                    "role": "assistant",
                    "content": preview_text,
                    "user_id": self.myid,
                    "item_id": item_id,
                    "timestamp": int(time.time() * 1000),
                }
            )
        self._update_chat_state(
            chat_id,
            ai_state="idle",
            generation_stage="",
            generation_started_at=None,
            preview_text="",
            countdown_until=None,
            cancelable=False,
            trigger_state="",
            pending_reason="confirmed_sent",
            active_generation=version,
        )

    def confirm_reply_threadsafe(self, chat_id: str, timeout: int = 8):
        if not self.event_loop:
            raise RuntimeError("运行循环尚未启动")
        fut = asyncio.run_coroutine_threadsafe(self.confirm_reply(chat_id), self.event_loop)
        return fut.result(timeout=timeout)

    def format_price(self, price):
        try:
            return round(float(price) / 100, 2)
        except (ValueError, TypeError):
            return 0.0

    def build_item_description(self, item_info):
        clean_skus = []
        raw_sku_list = item_info.get('skuList', [])

        for sku in raw_sku_list:
            specs = [p['valueText'] for p in sku.get('propertyList', []) if p.get('valueText')]
            spec_text = " ".join(specs) if specs else "默认规格"
            clean_skus.append({
                "spec": spec_text,
                "price": self.format_price(sku.get('price', 0)),
                "stock": sku.get('quantity', 0)
            })

        valid_prices = [s['price'] for s in clean_skus if s['price'] > 0]
        if valid_prices:
            min_price = min(valid_prices)
            max_price = max(valid_prices)
            if min_price == max_price:
                price_display = f"¥{min_price}"
            else:
                price_display = f"¥{min_price} - ¥{max_price}"
        else:
            main_price = round(float(item_info.get('soldPrice', 0)), 2)
            price_display = f"¥{main_price}"

        summary = {
            "title": item_info.get('title', ''),
            "desc": item_info.get('desc', ''),
            "price_range": price_display,
            "total_stock": item_info.get('quantity', 0),
            "sku_details": clean_skus
        }
        return json.dumps(summary, ensure_ascii=False)

    async def _load_item_info(self, item_id: str):
        item_info = await asyncio.to_thread(self.context_manager.get_item_info, item_id)
        if item_info:
            logger.info(f"从数据库获取商品信息: {item_id}")
            return item_info

        logger.info(f"从API获取商品信息: {item_id}")
        api_result = await asyncio.to_thread(self.xianyu.get_item_info, item_id)
        if 'data' in api_result and 'itemDO' in api_result['data']:
            item_info = api_result['data']['itemDO']
            await asyncio.to_thread(self.context_manager.save_item_info, item_id, item_info)
            return item_info
        logger.warning(f"获取商品信息失败: {api_result}")
        return None

    def _extract_latest_user_message(self, context):
        for msg in reversed(context):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""

    async def _wait_preview_window(self, chat_id: str, version: int) -> bool:
        deadline = int(time.time()) + self.reply_preview_seconds
        auto_reply_enabled = self._is_auto_reply_enabled()
        self._update_chat_state(
            chat_id,
            ai_state="preview_countdown",
            generation_stage="已生成回复，等待自动发送" if auto_reply_enabled else "已生成回复，请在倒计时内确认发送",
            countdown_until=deadline,
            cancelable=True,
            active_generation=version,
        )
        while time.time() < deadline:
            if self._is_generation_stale(chat_id, version):
                return False
            await asyncio.sleep(0.2)
        if self._is_generation_stale(chat_id, version):
            return False
        if self._is_auto_reply_enabled():
            return True
        self.chat_generation_enabled[chat_id] = False
        self._update_chat_state(
            chat_id,
            ai_state="idle",
            generation_stage="",
            generation_started_at=None,
            preview_text="",
            countdown_until=None,
            cancelable=False,
            trigger_state="",
            pending_reason="confirm_timeout",
            active_generation=version,
        )
        return False

    async def _send_trigger_messages(self, chat_id: str, toid: str, item_id: str, rule: dict, version: int) -> bool:
        await asyncio.to_thread(self.context_manager.mark_triggered_rule, chat_id, rule["id"])
        self._update_chat_state(
            chat_id,
            ai_state="trigger_replying",
            generation_stage="命中规则，正在直接回复",
            generation_started_at=int(time.time() * 1000),
            trigger_state=rule.get("name", rule["id"]),
            cancelable=False,
            active_generation=version,
        )
        for text in rule.get("messages", []):
            if self._is_generation_stale(chat_id, version):
                return False
            await self.send_msg(self.ws, chat_id, toid, text)
            await asyncio.to_thread(self.context_manager.add_message_by_chat, chat_id, self.myid, item_id, "assistant", text)
            if self.runtime_state:
                self.runtime_state.append_message_event(
                    {
                        "chat_id": chat_id,
                        "role": "assistant",
                        "content": text,
                        "user_id": self.myid,
                        "item_id": item_id,
                        "timestamp": int(time.time() * 1000),
                    }
                )
        self.chat_generation_enabled[chat_id] = False
        self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, trigger_state=rule.get("name", rule["id"]), preview_text="", countdown_until=None, cancelable=False, pending_reason="trigger_sent")
        return True

    async def _run_keyword_reply(self, chat_id: str, toid: str, item_id: str, rule: dict, version: int):
        try:
            await self._send_trigger_messages(chat_id, toid, item_id, rule, version)
        except Exception as e:
            logger.error(f"会话 {chat_id} 关键词直发异常: {e}")
            self._update_chat_state(chat_id, ai_state="error", generation_stage="", generation_started_at=None, cancelable=False, pending_reason=str(e))

    def _schedule_keyword_reply(self, chat_id: str, toid: str, item_id: str, rule: dict, reason: str = "keyword_direct_reply"):
        version = self._ensure_generation(chat_id)
        self.chat_generation_enabled[chat_id] = False
        self._update_chat_state(
            chat_id,
            ai_state="trigger_replying",
            generation_stage="命中关键词，正在中断 AI 并直接回复",
            generation_started_at=int(time.time() * 1000),
            preview_text="",
            countdown_until=None,
            cancelable=False,
            trigger_state=rule.get("name", rule["id"]),
            pending_reason=reason,
            active_generation=version,
        )
        self.chat_worker_tasks[chat_id] = asyncio.create_task(self._run_keyword_reply(chat_id, toid, item_id, rule, version))
        return version

    async def _process_chat_turn(self, chat_id: str):
        try:
            while True:
                version = self._current_generation(chat_id)
                if version <= 0 or not self.chat_generation_enabled.get(chat_id, False):
                    return

                toid = self.chat_peer_map.get(chat_id) or await asyncio.to_thread(self.context_manager.get_last_user_id_by_chat, chat_id)
                item_id = self.chat_item_map.get(chat_id) or await asyncio.to_thread(self.context_manager.get_last_item_id_by_chat, chat_id)
                if not toid or not item_id:
                    self.chat_generation_enabled[chat_id] = False
                    self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, preview_text="", countdown_until=None, cancelable=False, pending_reason="missing_target")
                    return

                current_state = self.get_chat_state(chat_id).get("ai_state", "idle")
                next_state = "restarting" if current_state in {"generating", "preview_countdown", "cancelled"} else "generating"
                self._update_chat_state(
                    chat_id,
                    ai_state=next_state,
                    generation_stage="正在读取商品信息",
                    generation_started_at=int(time.time() * 1000),
                    preview_text="",
                    countdown_until=None,
                    cancelable=True,
                    trigger_state="",
                    active_generation=version,
                )

                item_info = await self._load_item_info(item_id)
                if not item_info:
                    self.chat_generation_enabled[chat_id] = False
                    self._update_chat_state(chat_id, ai_state="error", generation_stage="", generation_started_at=None, cancelable=False, pending_reason="item_info_failed")
                    return
                if self._is_generation_stale(chat_id, version):
                    continue

                item_description = f"当前商品的信息如下：{self.build_item_description(item_info)}"
                user_message_count = await asyncio.to_thread(self.context_manager.get_user_message_count_by_chat, chat_id)
                matched_rule = None
                self._update_chat_state(chat_id, generation_stage="正在整理聊天上下文", active_generation=version)
                context = await asyncio.to_thread(self.context_manager.get_context_by_chat, chat_id)
                latest_user_message = self._extract_latest_user_message(context)

                if user_message_count == 1:
                    matched_rule = await asyncio.to_thread(self.trigger_rule_store.match_first_rule, item_description, True)
                    if matched_rule:
                        already_triggered = await asyncio.to_thread(self.context_manager.has_triggered_rule, chat_id, matched_rule["id"])
                        if already_triggered:
                            matched_rule = None

                if matched_rule:
                    sent = await self._send_trigger_messages(chat_id, toid, item_id, matched_rule, version)
                    if not sent or self._is_generation_stale(chat_id, version):
                        continue
                    return

                if self._detect_after_sales(latest_user_message):
                    logger.info(f"检测到售后意图，会话 {chat_id} 立即暂停AI回复30分钟")
                    self._set_chat_ai_pause(chat_id, 1800, "after_sales")
                    if self.runtime_state:
                        self.runtime_state.publish("after_sales_alert", {"chat_id": chat_id})
                    self.chat_generation_enabled[chat_id] = False
                    self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, preview_text="", countdown_until=None, cancelable=False, pending_reason="after_sales_paused", active_generation=version)
                    return

                self._update_chat_state(chat_id, generation_stage="正在调用 AI 生成回复", active_generation=version)
                reply_result = await asyncio.to_thread(bot.generate_reply, latest_user_message, item_description, context)
                if self.runtime_state:
                    self.runtime_state.update_status(llm_last_ok_at=int(time.time()), llm_last_error="")
                if self._is_generation_stale(chat_id, version):
                    continue

                bot_reply = reply_result.get("reply", "")
                intent = reply_result.get("intent", "default")
                if bot_reply == "-":
                    self.chat_generation_enabled[chat_id] = False
                    self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, preview_text="", countdown_until=None, cancelable=False, pending_reason="no_reply")
                    if self._is_generation_stale(chat_id, version):
                        continue
                    return

                self._update_chat_state(chat_id, ai_state="preview_countdown", generation_stage="已生成回复，等待自动发送", preview_text=bot_reply, cancelable=True, active_generation=version, pending_reason="")
                if not await self._wait_preview_window(chat_id, version):
                    continue
                if self._is_generation_stale(chat_id, version):
                    continue

                await self.send_msg(self.ws, chat_id, toid, bot_reply)
                await asyncio.to_thread(self.context_manager.add_message_by_chat, chat_id, self.myid, item_id, "assistant", bot_reply)
                if intent == "price":
                    await asyncio.to_thread(self.context_manager.increment_bargain_count_by_chat, chat_id)

                logger.info(f"机器人回复: {bot_reply}")
                if self.runtime_state:
                    self.runtime_state.append_message_event(
                        {
                            "chat_id": chat_id,
                            "role": "assistant",
                            "content": bot_reply,
                            "user_id": self.myid,
                            "item_id": item_id,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                self.chat_generation_enabled[chat_id] = False
                self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, preview_text="", countdown_until=None, cancelable=False, trigger_state="", pending_reason="sent", active_generation=version)
                if self._is_generation_stale(chat_id, version):
                    continue
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"会话 {chat_id} 回复任务异常: {e}")
            if self.runtime_state:
                self.runtime_state.update_status(llm_last_error=str(e))
            self.chat_generation_enabled[chat_id] = False
            self._update_chat_state(chat_id, ai_state="error", generation_stage="", generation_started_at=None, cancelable=False, pending_reason=str(e))

    def _schedule_chat_processing(self, chat_id: str, reason: str = "new_message"):
        version = self._ensure_generation(chat_id)
        self.chat_generation_enabled[chat_id] = True
        worker = self.chat_worker_tasks.get(chat_id)
        if worker is None or worker.done():
            self.chat_worker_tasks[chat_id] = asyncio.create_task(self._process_chat_turn(chat_id))
            self._update_chat_state(chat_id, ai_state="generating", generation_stage="收到消息，准备开始处理", generation_started_at=int(time.time() * 1000), cancelable=True, pending_reason=reason, active_generation=version)
        else:
            self._update_chat_state(chat_id, ai_state="restarting", generation_stage="收到新消息，正在打断上一轮并重新处理", generation_started_at=int(time.time() * 1000), cancelable=True, pending_reason=reason, active_generation=version)
        return version

    async def handle_message(self, message_data, websocket):
        try:
            try:
                message = message_data
                ack = {
                    "code": 200,
                    "headers": {
                        "mid": message["headers"]["mid"] if "mid" in message["headers"] else generate_mid(),
                        "sid": message["headers"]["sid"] if "sid" in message["headers"] else '',
                    }
                }
                if 'app-key' in message["headers"]:
                    ack["headers"]["app-key"] = message["headers"]["app-key"]
                if 'ua' in message["headers"]:
                    ack["headers"]["ua"] = message["headers"]["ua"]
                if 'dt' in message["headers"]:
                    ack["headers"]["dt"] = message["headers"]["dt"]
                await websocket.send(json.dumps(ack))
            except Exception:
                pass

            if not self.is_sync_package(message_data):
                return

            sync_data = message_data["body"]["syncPushPackage"]["data"][0]
            if "data" not in sync_data:
                logger.debug("同步包中无data字段")
                return

            try:
                data = sync_data["data"]
                try:
                    data = base64.b64decode(data).decode("utf-8")
                    message = json.loads(data)
                except Exception:
                    decrypted_data = decrypt(data)
                    message = json.loads(decrypted_data)
            except Exception as e:
                logger.error(f"消息解密失败: {e}")
                return

            try:
                if message['3']['redReminder'] == '等待买家付款':
                    user_id = message['1'].split('@')[0]
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'等待买家 {user_url} 付款')
                    return
                if message['3']['redReminder'] == '交易关闭':
                    user_id = message['1'].split('@')[0]
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'买家 {user_url} 交易关闭')
                    return
                if message['3']['redReminder'] == '等待卖家发货':
                    user_id = message['1'].split('@')[0]
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'交易成功 {user_url} 等待卖家发货')
                    return
            except Exception:
                pass

            if self.is_typing_status(message):
                logger.debug("用户正在输入")
                return
            if not self.is_chat_message(message):
                logger.debug("其他非聊天消息")
                logger.debug(f"原始消息: {message}")
                return

            create_time = int(message["1"]["5"])
            send_user_name = message["1"]["10"]["reminderTitle"]
            send_user_id = message["1"]["10"]["senderUserId"]
            send_message = message["1"]["10"]["reminderContent"]
            if (time.time() * 1000 - create_time) > self.message_expire_time:
                logger.debug("过期消息丢弃")
                return

            url_info = message["1"]["10"]["reminderUrl"]
            item_id = url_info.split("itemId=")[1].split("&")[0] if "itemId=" in url_info else None
            chat_id = message["1"]["2"].split('@')[0]
            self.chat_peer_map[chat_id] = send_user_id
            if item_id:
                self.chat_item_map[chat_id] = item_id

            if not item_id:
                logger.warning("无法获取商品ID")
                return

            if send_user_id == self.myid:
                await asyncio.to_thread(self.context_manager.add_message_by_chat, chat_id, self.myid, item_id, "assistant", send_message)
                logger.info(f"卖家人工回复 (会话: {chat_id}, 商品: {item_id}): {send_message}")
                return

            logger.info(f"用户: {send_user_name} (ID: {send_user_id}), 商品: {item_id}, 会话: {chat_id}, 消息: {send_message}")
            if self.runtime_state:
                self.runtime_state.append_message_event(
                    {
                        "chat_id": chat_id,
                        "role": "user",
                        "content": send_message,
                        "user_id": send_user_id,
                        "item_id": item_id,
                        "timestamp": create_time,
                    }
                )
            await asyncio.to_thread(self.context_manager.add_message_by_chat, chat_id, send_user_id, item_id, "user", send_message)
            self._update_chat_state(chat_id, last_user_message_at=create_time, pending_reason="new_message")
            if self.is_bracket_system_message(send_message):
                logger.info(f"检测到系统消息：'{send_message}'，跳过自动回复")
                self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, cancelable=False, pending_reason="bracket_system")
                return
            if self.is_system_message(message):
                logger.debug("系统消息，跳过处理")
                self._update_chat_state(chat_id, ai_state="idle", generation_stage="", generation_started_at=None, cancelable=False, pending_reason="system_message")
                return

            matched_keyword_rule = await asyncio.to_thread(self.keyword_reply_rule_store.match_first_rule, send_message, False)
            if matched_keyword_rule:
                already_triggered = await asyncio.to_thread(self.context_manager.has_triggered_rule, chat_id, matched_keyword_rule["id"])
                if not already_triggered:
                    self._schedule_keyword_reply(chat_id, send_user_id, item_id, matched_keyword_rule, reason="keyword_direct_reply")
                    return

            if self._is_chat_ai_paused(chat_id):
                logger.info(f"会话 {chat_id} AI处于暂停状态，跳过自动回复")
                self._update_chat_state(chat_id, ai_state="idle", generation_stage="", cancelable=False, pending_reason="ai_paused")
                return

            self._schedule_chat_processing(chat_id, reason="new_message")
        except Exception as e:
            logger.error(f"处理消息时发生错误: {str(e)}")
            logger.debug(f"原始消息: {message_data}")
            if self.runtime_state:
                self.runtime_state.update_status(llm_last_error=str(e))

    async def send_heartbeat(self, ws):
        try:
            heartbeat_mid = generate_mid()
            heartbeat_msg = {
                "lwp": "/!",
                "headers": {
                    "mid": heartbeat_mid
                }
            }
            await ws.send(json.dumps(heartbeat_msg))
            self.last_heartbeat_time = time.time()
            logger.debug("心跳包已发送")
            return heartbeat_mid
        except Exception as e:
            logger.error(f"发送心跳包失败: {e}")
            raise

    async def heartbeat_loop(self, ws):
        while True:
            try:
                current_time = time.time()
                if current_time - self.last_heartbeat_time >= self.heartbeat_interval:
                    await self.send_heartbeat(ws)
                if (current_time - self.last_heartbeat_response) > (self.heartbeat_interval + self.heartbeat_timeout):
                    logger.warning("心跳响应超时，可能连接已断开")
                    break
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"心跳循环出错: {e}")
                break

    async def handle_heartbeat_response(self, message_data):
        try:
            if (
                isinstance(message_data, dict)
                and "headers" in message_data
                and "mid" in message_data["headers"]
                and "code" in message_data
                and message_data["code"] == 200
            ):
                self.last_heartbeat_response = time.time()
                logger.debug("收到心跳响应")
                if self.runtime_state:
                    self.runtime_state.update_status(last_heartbeat_at=int(self.last_heartbeat_response))
                return True
        except Exception as e:
            logger.error(f"处理心跳响应出错: {e}")
        return False

    async def _cancel_all_workers(self):
        tasks = [task for task in self.chat_worker_tasks.values() if task and not task.done()]
        self.chat_worker_tasks = {}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def main(self):
        self.event_loop = asyncio.get_running_loop()
        self.cookie_refresh_event = asyncio.Event()
        while True:
            try:
                self.connection_restart_flag = False
                headers = {
                    "Cookie": self.cookies_str,
                    "Host": "wss-goofish.dingtalk.com",
                    "Connection": "Upgrade",
                    "Pragma": "no-cache",
                    "Cache-Control": "no-cache",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                    "Origin": "https://www.goofish.com",
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                }

                async with websockets.connect(self.base_url, extra_headers=headers) as websocket:
                    self.ws = websocket
                    if self.runtime_state:
                        self.runtime_state.update_status(ws_connected=True)
                    await self.init(websocket)
                    self.last_heartbeat_time = time.time()
                    self.last_heartbeat_response = time.time()
                    self.heartbeat_task = asyncio.create_task(self.heartbeat_loop(websocket))
                    self.token_refresh_task = asyncio.create_task(self.token_refresh_loop())

                    async for message in websocket:
                        try:
                            if self.connection_restart_flag:
                                logger.info("检测到连接重启标志，准备重新建立连接...")
                                break

                            message_data = json.loads(message)
                            if await self.handle_heartbeat_response(message_data):
                                continue

                            if "headers" in message_data and "mid" in message_data["headers"]:
                                ack = {
                                    "code": 200,
                                    "headers": {
                                        "mid": message_data["headers"]["mid"],
                                        "sid": message_data["headers"].get("sid", "")
                                    }
                                }
                                for key in ["app-key", "ua", "dt"]:
                                    if key in message_data["headers"]:
                                        ack["headers"][key] = message_data["headers"][key]
                                await websocket.send(json.dumps(ack))

                            await self.handle_message(message_data, websocket)
                        except json.JSONDecodeError:
                            logger.error("消息解析失败")
                        except Exception as e:
                            logger.error(f"处理消息时发生错误: {str(e)}")
                            logger.debug(f"原始消息: {message}")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket连接已关闭")
                if self.runtime_state:
                    self.runtime_state.update_status(ws_connected=False)
            except Exception as e:
                logger.error(f"连接发生错误: {e}")
                if self.runtime_state:
                    self.runtime_state.update_status(ws_connected=False)
            finally:
                if self.runtime_state:
                    self.runtime_state.update_status(ws_connected=False)
                if self.heartbeat_task:
                    self.heartbeat_task.cancel()
                    try:
                        await self.heartbeat_task
                    except asyncio.CancelledError:
                        pass
                if self.token_refresh_task:
                    self.token_refresh_task.cancel()
                    try:
                        await self.token_refresh_task
                    except asyncio.CancelledError:
                        pass
                await self._cancel_all_workers()
                if self.connection_restart_flag:
                    logger.info("主动重启连接，立即重连...")
                else:
                    logger.info("等待5秒后重连...")
                    await asyncio.sleep(5)



def check_and_complete_env(cookie_source: str = "env", allow_console: bool = True):
    """检查并补全关键环境变量"""
    env_path = get_env_file_path()
    updated = False

    # 始终需要API_KEY
    critical_vars = {
        "API_KEY": "默认使用通义千问,apikey通过百炼模型平台获取",
    }
    if cookie_source != "plugin":
        critical_vars["COOKIES_STR"] = "your_cookies_here"

    for key, placeholder in critical_vars.items():
        curr_val = os.getenv(key)
        if not curr_val or curr_val == placeholder:
            if not allow_console:
                logger.error(
                    f"配置项 [{key}] 未设置或为占位值，"
                    f"请在对应的 .env 文件中添加 {key}=... 后重启实例"
                )
                continue
            logger.warning(f"配置项 [{key}] 未设置或为默认值，请输入")
            while True:
                val = input(f"请输入 {key}: ").strip()
                if val:
                    os.environ[key] = val
                    try:
                        if not env_path.exists():
                            with open(env_path, 'w', encoding='utf-8') as f:
                                pass
                        set_key(str(env_path), key, val)
                        updated = True
                    except Exception as e:
                        logger.warning(f"无法自动写入.env文件，请手动保存: {e}")
                    break
                print(f"{key} 不能为空，请重新输入")

    if updated:
        logger.info("新的配置已保存/更新至 .env 文件中")

    # env模式下仍执行原有Cookie校验
    if cookie_source != "plugin":
        while True:
            cookies_str = os.getenv("COOKIES_STR", "")
            cookies = trans_cookies(cookies_str)
            if cookies.get("unb"):
                break

            logger.warning("COOKIES_STR 无效或缺少 unb 字段，请重新输入完整Cookie")
            new_cookie = input("请输入 COOKIES_STR: ").strip()
            if not new_cookie:
                print("COOKIES_STR 不能为空，请重新输入")
                continue

            os.environ["COOKIES_STR"] = new_cookie
            try:
                if not env_path.exists():
                    with open(env_path, 'w', encoding='utf-8') as f:
                        pass
                set_key(str(env_path), "COOKIES_STR", new_cookie)
                logger.info("COOKIES_STR 已更新到 .env")
            except Exception as e:
                logger.warning(f"无法自动写入.env文件，请手动保存: {e}")


def wait_cookie_from_plugin(
    bridge: CookieBridgeServer,
    timeout_seconds: int,
    baseline_cookie: Optional[str] = None,
    require_change: bool = False,
    reason: str = "startup",
    runtime_state: Optional[RuntimeState] = None,
) -> Optional[str]:
    logger.info(f"等待浏览器插件推送Cookie（原因: {reason}, 超时: {timeout_seconds}s）")
    if runtime_state:
        runtime_state.update_status(cookie_waiting=True, cookie_wait_reason=reason)
    cookie = bridge.wait_for_cookie(
        timeout_seconds=timeout_seconds,
        baseline_cookie=baseline_cookie,
        require_change=require_change,
    )
    if cookie:
        logger.success("已接收插件Cookie")
        if runtime_state:
            runtime_state.update_status(
                cookie_waiting=False,
                cookie_wait_reason="",
                last_cookie_update_at=int(time.time()),
                cookie_error="",
            )
    else:
        logger.warning("等待插件Cookie超时")
        if runtime_state:
            timeout_message = (
                "等待浏览器插件同步 Cookie 超时，请确认当前项目的 Bridge Token / Project ID 配置正确，"
                "并在对应账号的闲鱼消息页完成登录或滑块后重试。"
            )
            runtime_state.update_status(
                cookie_waiting=False,
                cookie_wait_reason=reason,
                cookie_error=timeout_message,
            )
    return cookie


def build_cookie_refresh_callback(bridge: CookieBridgeServer, runtime_state: Optional[RuntimeState] = None):
    def _refresh_cookie(reason: str, current_cookie: str) -> Optional[str]:
        timeout_seconds = int(os.getenv("COOKIE_WAIT_TIMEOUT", "300"))
        require_change = os.getenv("COOKIE_REQUIRE_CHANGE", "True").lower() == "true"
        return wait_cookie_from_plugin(
            bridge=bridge,
            timeout_seconds=timeout_seconds,
            baseline_cookie=current_cookie,
            require_change=require_change,
            reason=reason,
            runtime_state=runtime_state,
        )

    return _refresh_cookie


def configure_environment_and_logging():
    env_path = get_env_file_path()
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"已加载环境配置: {env_path.name}")

    for example_path in get_env_example_paths():
        if example_path.exists():
            load_dotenv(example_path)
            logger.info(f"已加载默认配置: {example_path.name}")

    # 配置日志级别
    log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
    logger.remove()  # 移除默认handler
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.info(f"日志级别设置为: {log_level}")


def bootstrap_runtime(
    runtime_state: Optional[RuntimeState] = None,
    allow_console_fallback: bool = True,
    trigger_rule_store: Optional[TriggerRuleStore] = None,
    keyword_reply_rule_store: Optional[KeywordReplyRuleStore] = None,
):
    global bot
    configure_environment_and_logging()

    cookie_source = os.getenv("COOKIE_SOURCE", "env").lower()
    cookie_project_id = os.getenv("COOKIE_PROJECT_ID", "").strip()
    cookie_account_hint = os.getenv("COOKIE_ACCOUNT_HINT", "").strip()
    bridge = None
    cookie_refresh_callback = None

    if runtime_state:
        runtime_state.update_status(
            cookie_source=cookie_source,
            project_id=cookie_project_id,
            account_hint=cookie_account_hint,
            instance_name=get_instance_name(),
            browser_name=get_browser_name(),
            env_file=get_env_file_path().name,
        )

    if cookie_source == "plugin":
        bridge_host = os.getenv("COOKIE_BRIDGE_HOST", "127.0.0.1")
        bridge_port = int(os.getenv("COOKIE_BRIDGE_PORT", "18765"))
        bridge_token = os.getenv("COOKIE_BRIDGE_TOKEN", "")
        if not bridge_token:
            logger.warning("COOKIE_BRIDGE_TOKEN 未配置，桥接接口将不做鉴权（仅限本机开发环境）")

        bridge = CookieBridgeServer(
            host=bridge_host,
            port=bridge_port,
            token=bridge_token,
            env_path=str(get_env_file_path()),
            project_id=cookie_project_id,
            account_hint=cookie_account_hint,
        )
        bridge.start()
        if runtime_state:
            runtime_state.update_status(bridge_online=True)
        cookie_refresh_callback = build_cookie_refresh_callback(bridge, runtime_state=runtime_state)

    # 交互式检查并补全配置（UI模式下不阻塞）
    check_and_complete_env(cookie_source=cookie_source, allow_console=allow_console_fallback)

    cookies_str = os.getenv("COOKIES_STR", "")
    if cookie_source == "plugin":
        timeout_seconds = int(os.getenv("COOKIE_WAIT_TIMEOUT", "300"))
        baseline_hash = cookie_hash(cookies_str) if cookies_str else None
        if baseline_hash:
            logger.info("检测到已有Cookie，将等待插件推送“变化后的Cookie”")
        else:
            logger.info("未检测到本地Cookie，将等待插件推送")

        plugin_cookie = wait_cookie_from_plugin(
            bridge=bridge,
            timeout_seconds=timeout_seconds,
            baseline_cookie=cookies_str or None,
            require_change=bool(baseline_hash),
            reason="startup",
            runtime_state=runtime_state,
        )
        if plugin_cookie:
            cookies_str = plugin_cookie
            os.environ["COOKIES_STR"] = plugin_cookie
        else:
            logger.warning("插件Cookie未就绪，回退使用当前.env中的COOKIES_STR")

    bot = XianyuReplyBot()
    xianyu_live = XianyuLive(
        cookies_str,
        cookie_refresh_callback=cookie_refresh_callback,
        runtime_state=runtime_state,
        trigger_rule_store=trigger_rule_store or TriggerRuleStore(),
        keyword_reply_rule_store=keyword_reply_rule_store or KeywordReplyRuleStore(),
    )
    xianyu_live.xianyu.allow_manual_cookie_input = allow_console_fallback
    return xianyu_live, bridge


if __name__ == '__main__':
    bridge = None
    try:
        xianyuLive, bridge = bootstrap_runtime(runtime_state=None)
        asyncio.run(xianyuLive.main())
    finally:
        if bridge:
            bridge.stop()
