import json
import os
import threading
import re
from typing import Dict, List, Optional
from project_paths import get_data_file_path, resolve_project_path


VISION_PRO_MESSAGES = [
    "\n".join(
        [
            "急用/看Cortis 选 Vision Pro无锁版📱提供美服账号",
            "✅ 省外3天起租 ¥360/3天 (比有锁贵)",
            "📅 北京1天起 津冀2天起",
            "❗️档期无锁1→3月16到",
            "无锁2→3月15到",
            "半锁需等4月2",
            "🗓️ 美版有锁便宜 3月16日到",
            "📞 联系：点头像搜Vision → 66.66链接聊",
            "(鱼显示价为30天起价)",
            "操作：66链接➡️立即租➡️选档期➡️看到价",
            "❗️先看这段话里的档期和价格，能接受咱再往下",
        ]
    ),
    "\n".join(
        [
            "要租赁的先看一下上面那段和这个",
            "辛苦回复一下以下问题",
            "1.什么时候用的档期？我好看是否有",
            "2.租赁什么用途？如考试或演讲 好给你推荐合适的（类型是机考纸考？大学生考/企业内考？）",
            "3.标价是30天一天的价，闲鱼是按照最低价来显示，你点立即租，选择3天就能看得到相应的价格。租赁不讲价，能接受再问",
            "省外是3天起租 北京一天起 天津河北次日早上达2天起",
        ]
    ),
]

DEPOSIT_FREE_MESSAGES = [
    "\n".join(
        [
            "租赁方面你点 立即租 选择相应套餐天数就能看到租金价了❗️租赁不讲价 能接受价再聊哈宝宝",
            "京津冀外3天起租 例如11 12 13那就是确保10或者最晚11号收到快递，13号下午或14号寄回即可，若租期第一天没收到晚一天寄回就行",
            "",
            "发货须知: 各付一程顺丰/京东运费+一千的保价，一般2 3块，避免争议我这边拍发货视频，您收到和寄出也拍摄开箱和发货视频，使用保护好一点不产生磨损刮伤功能损坏就不会扣押金哈",
        ]
    ),
    "\n".join(
        [
            "要租赁的先看一下上面那段和这个",
            "辛苦回复一下以下问题",
            "1.什么时候用的档期？我好看是否有",
            "2.租赁什么用途？如考试或演讲 好给你推荐合适的（类型是机考纸考？大学生考/企业内考？）",
            "3.标价是30天一天的价，闲鱼是按照最低价来显示，你点立即租，选择3天就能看得到相应的价格。租赁不讲价，能接受再问",
            "省外是3天起租 北京一天起 天津河北次日早上达2天起",
        ]
    ),
]


DEFAULT_TRIGGER_RULES = [
    {
        "id": "vision_pro",
        "name": "Vision Pro 新对话首条自动回复",
        "enabled": True,
        "priority": 10,
        "first_message_only": True,
        "match_field": "item_description",
        "match_type": "contains",
        "pattern": "vision pro",
        "messages": VISION_PRO_MESSAGES,
    },
    {
        "id": "deposit_free_rental",
        "name": "免押租赁 新对话首条自动回复",
        "enabled": True,
        "priority": 20,
        "first_message_only": True,
        "match_field": "item_description",
        "match_type": "contains",
        "pattern": "免押租赁",
        "messages": DEPOSIT_FREE_MESSAGES,
    },
]


DEPOSIT_KEYWORD_MESSAGES = [
    "\n".join(
        [
            "免押方面：",
            "至少芝麻信用分儿需要550分整及以上，而且最近有使用过支付宝买卖东西不能太不活跃",
            "免押需要服务费，是免押萍台扣我们的。我看有一个人免押和非免押差价都差100了，我这里5000以下的免押服务费20，五千及以上的35加在租金里",
            "这边把那个支付宝二维码给您一发，完了闲鱼上面改为一块押金就行了，双方拍摄好验货与发货视频，不懂的可以问不要私自做决定导致外观损坏或者功能异常，验货没问题给您免押完结",
        ]
    ),
    "\n".join(
        [
            "免押流程：",
            "和买东西拍下后不付款卖家改价一样",
            "点立即租 确认租 选择要用的时间档期❗️注意下一步地址一定要选对，租赁地址改不了",
            "确认租点完以后 到付款那里先点确定，",
            "输入密码那一步一定点 叉掉 这样我就可以改押金价格与租金价格了",
            "随后你付款即可（需要免押的等下支付宝扫免押码即完成免押）",
        ]
    ),
]


DEFAULT_KEYWORD_REPLY_RULES = [
    {
        "id": "deposit_keyword_reply",
        "name": "免押/押金 首次识别直发",
        "enabled": True,
        "priority": 10,
        "first_message_only": False,
        "match_field": "user_message",
        "match_type": "contains",
        "pattern": "免押,押金",
        "messages": DEPOSIT_KEYWORD_MESSAGES,
    },
]


class BaseRuleStore:
    def __init__(self, path: str, default_rules: Optional[List[Dict]] = None):
        self.path = path
        self.default_rules = list(default_rules or [])
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        folder = os.path.dirname(self.path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)
        if not os.path.exists(self.path):
            self._write(self.default_rules)

    def _read(self) -> List[Dict]:
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return self._normalize(data)

    def _write(self, data: List[Dict]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _normalize(self, items: List[Dict]) -> List[Dict]:
        normalized = []
        for idx, row in enumerate(items):
            if not isinstance(row, dict):
                continue
            messages = row.get("messages", [])
            if isinstance(messages, str):
                messages = [messages]
            messages = [str(text).strip() for text in messages if str(text).strip()]
            if not messages:
                continue
            normalized.append(
                {
                    "id": str(row.get("id", f"rule_{idx + 1}")),
                    "name": str(row.get("name", f"规则{idx + 1}")).strip() or f"规则{idx + 1}",
                    "enabled": bool(row.get("enabled", True)),
                    "priority": int(row.get("priority", (idx + 1) * 10)),
                    "first_message_only": bool(row.get("first_message_only", True)),
                    "match_field": str(row.get("match_field", "item_description")).strip() or "item_description",
                    "match_type": str(row.get("match_type", "contains")).strip() or "contains",
                    "pattern": str(row.get("pattern", "")).strip(),
                    "messages": messages,
                }
            )
        normalized.sort(key=lambda item: (item["priority"], item["id"]))
        return normalized

    def list(self) -> List[Dict]:
        with self._lock:
            return self._read()

    def replace(self, items: List[Dict]) -> List[Dict]:
        with self._lock:
            normalized = self._normalize(items)
            for idx, row in enumerate(normalized):
                row["priority"] = (idx + 1) * 10
            self._write(normalized)
            return normalized

    def _normalize_for_match(self, text: str) -> str:
        return re.sub(r"\s+", "", (text or "").lower())

    def _build_pattern_candidates(self, pattern: str) -> List[str]:
        raw = (pattern or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in re.split(r"[,\n|;；，]+", raw) if p.strip()]
        candidates = list(parts)
        if len(parts) == 1 and " " in parts[0]:
            candidates.extend([s.strip() for s in parts[0].split(" ") if len(s.strip()) >= 2])
        dedup = []
        seen = set()
        for item in candidates:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        return dedup

    def match_first_rule(self, text: str, is_first_message: bool) -> Optional[Dict]:
        content = (text or "").lower()
        content_compact = self._normalize_for_match(text or "")
        for rule in self.list():
            if not rule.get("enabled", True):
                continue
            if rule.get("first_message_only", True) and not is_first_message:
                continue
            candidates = self._build_pattern_candidates(str(rule.get("pattern", "")))
            if not candidates:
                continue
            match_type = str(rule.get("match_type", "contains")).lower()
            if match_type == "contains":
                for candidate in candidates:
                    target = candidate.lower()
                    if target in content or self._normalize_for_match(candidate) in content_compact:
                        return rule
        return None


class TriggerRuleStore(BaseRuleStore):
    def __init__(self, path: str | None = None):
        default_path = get_data_file_path("trigger_rules.json")
        super().__init__(path=str(resolve_project_path(path or str(default_path))), default_rules=DEFAULT_TRIGGER_RULES)


class KeywordReplyRuleStore(BaseRuleStore):
    def __init__(self, path: str | None = None):
        default_path = get_data_file_path("keyword_reply_rules.json")
        super().__init__(path=str(resolve_project_path(path or str(default_path))), default_rules=DEFAULT_KEYWORD_REPLY_RULES)
