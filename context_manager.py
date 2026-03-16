import sqlite3
import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from loguru import logger
from project_paths import get_data_file_path, get_project_root, resolve_project_path


class ChatContextManager:
    """
    聊天上下文管理器
    
    负责存储和检索用户与商品之间的对话历史，使用SQLite数据库进行持久化存储。
    支持按会话ID检索对话历史，以及议价次数统计。
    """
    
    def __init__(self, max_history=100, db_path=None):
        """
        初始化聊天上下文管理器
        
        Args:
            max_history: 每个对话保留的最大消息数
            db_path: SQLite数据库文件路径
        """
        self.max_history = max_history
        default_db_path = get_data_file_path("chat_history.db")
        self.db_path = str(resolve_project_path(db_path or str(default_db_path)))
        self._seed_from_legacy_db_if_needed()
        self._init_db()

    def _count_messages(self, db_path: str) -> int:
        if not os.path.exists(db_path):
            return 0
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(1)
                FROM sqlite_master
                WHERE type = 'table' AND name = 'messages'
                """
            )
            table_row = cursor.fetchone()
            if not table_row or int(table_row[0]) <= 0:
                return 0
            cursor.execute("SELECT COUNT(1) FROM messages")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    def _seed_from_legacy_db_if_needed(self):
        current_path = Path(self.db_path).resolve()
        legacy_path = (get_project_root() / "data" / "chat_history.db").resolve()
        if current_path == legacy_path:
            return
        if current_path.parent.name not in {"edge", "chrome"}:
            return
        if not legacy_path.exists():
            return

        current_count = self._count_messages(str(current_path))
        legacy_count = self._count_messages(str(legacy_path))
        if current_count > 0 or legacy_count <= 0:
            return

        current_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(legacy_path), str(current_path))
        logger.info(f"已从旧聊天库迁移初始会话数据: {legacy_path} -> {current_path}")
        
    def _init_db(self):
        """初始化数据库表结构"""
        # 确保数据库目录存在
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建消息表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            chat_id TEXT
        )
        ''')
        
        # 检查是否需要添加chat_id字段（兼容旧数据库）
        cursor.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'chat_id' not in columns:
            cursor.execute('ALTER TABLE messages ADD COLUMN chat_id TEXT')
            logger.info("已为messages表添加chat_id字段")
        
        # 创建索引以加速查询
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_user_item ON messages (user_id, item_id)
        ''')
        
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_chat_id ON messages (chat_id)
        ''')
        
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_timestamp ON messages (timestamp)
        ''')
        
        # 创建基于会话ID的议价次数表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_bargain_counts (
            chat_id TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建商品信息表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS items (
            item_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            price REAL,
            description TEXT,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_trigger_rules (
            chat_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, rule_id)
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"聊天历史数据库初始化完成: {self.db_path}")
        

            
    def save_item_info(self, item_id, item_data):
        """
        保存商品信息到数据库
        
        Args:
            item_id: 商品ID
            item_data: 商品信息字典
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 从商品数据中提取有用信息
            price = float(item_data.get('soldPrice', 0))
            description = item_data.get('desc', '')
            
            # 将整个商品数据转换为JSON字符串
            data_json = json.dumps(item_data, ensure_ascii=False)
            
            cursor.execute(
                """
                INSERT INTO items (item_id, data, price, description, last_updated) 
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id) 
                DO UPDATE SET data = ?, price = ?, description = ?, last_updated = ?
                """,
                (
                    item_id, data_json, price, description, datetime.now().isoformat(),
                    data_json, price, description, datetime.now().isoformat()
                )
            )
            
            conn.commit()
            logger.debug(f"商品信息已保存: {item_id}")
        except Exception as e:
            logger.error(f"保存商品信息时出错: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def get_item_info(self, item_id):
        """
        从数据库获取商品信息
        
        Args:
            item_id: 商品ID
            
        Returns:
            dict: 商品信息字典，如果不存在返回None
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT data FROM items WHERE item_id = ?",
                (item_id,)
            )
            
            result = cursor.fetchone()
            if result:
                return json.loads(result[0])
            return None
        except Exception as e:
            logger.error(f"获取商品信息时出错: {e}")
            return None
        finally:
            conn.close()

    def add_message_by_chat(self, chat_id, user_id, item_id, role, content):
        """
        基于会话ID添加新消息到对话历史
        
        Args:
            chat_id: 会话ID
            user_id: 用户ID (用户消息存真实user_id，助手消息存卖家ID)
            item_id: 商品ID
            role: 消息角色 (user/assistant)
            content: 消息内容
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 插入新消息，使用chat_id作为额外标识
            cursor.execute(
                "INSERT INTO messages (user_id, item_id, role, content, timestamp, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, item_id, role, content, datetime.now().isoformat(), chat_id)
            )
            
            # 检查是否需要清理旧消息（基于chat_id）
            cursor.execute(
                """
                SELECT id FROM messages 
                WHERE chat_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?, 1
                """, 
                (chat_id, self.max_history)
            )
            
            oldest_to_keep = cursor.fetchone()
            if oldest_to_keep:
                cursor.execute(
                    "DELETE FROM messages WHERE chat_id = ? AND id < ?",
                    (chat_id, oldest_to_keep[0])
                )
            
            conn.commit()
        except Exception as e:
            logger.error(f"添加消息到数据库时出错: {e}")
            conn.rollback()
        finally:
            conn.close()

    def get_context_by_chat(self, chat_id):
        """
        基于会话ID获取对话历史
        
        Args:
            chat_id: 会话ID
            
        Returns:
            list: 包含对话历史的列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                """
                SELECT role, content FROM messages 
                WHERE chat_id = ? 
                ORDER BY timestamp ASC
                LIMIT ?
                """, 
                (chat_id, self.max_history)
            )
            
            messages = [{"role": role, "content": content} for role, content in cursor.fetchall()]
            
            # 获取议价次数并添加到上下文中
            bargain_count = self.get_bargain_count_by_chat(chat_id)
            if bargain_count > 0:
                messages.append({
                    "role": "system", 
                    "content": f"议价次数: {bargain_count}"
                })
            
        except Exception as e:
            logger.error(f"获取对话历史时出错: {e}")
            messages = []
        finally:
            conn.close()
        
        return messages

    def increment_bargain_count_by_chat(self, chat_id):
        """
        基于会话ID增加议价次数
        
        Args:
            chat_id: 会话ID
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 使用UPSERT语法直接基于chat_id增加议价次数
            cursor.execute(
                """
                INSERT INTO chat_bargain_counts (chat_id, count, last_updated)
                VALUES (?, 1, ?)
                ON CONFLICT(chat_id) 
                DO UPDATE SET count = count + 1, last_updated = ?
                """,
                (chat_id, datetime.now().isoformat(), datetime.now().isoformat())
            )
            
            conn.commit()
            logger.debug(f"会话 {chat_id} 议价次数已增加")
        except Exception as e:
            logger.error(f"增加议价次数时出错: {e}")
            conn.rollback()
        finally:
            conn.close()

    def get_bargain_count_by_chat(self, chat_id):
        """
        基于会话ID获取议价次数
        
        Args:
            chat_id: 会话ID
            
        Returns:
            int: 议价次数
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT count FROM chat_bargain_counts WHERE chat_id = ?",
                (chat_id,)
            )
            
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"获取议价次数时出错: {e}")
            return 0
        finally:
            conn.close() 

    def get_chat_list(self, limit=100):
        """获取会话列表（按最后消息时间倒序）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT m.chat_id, m.item_id, m.content, m.timestamp
                FROM messages m
                INNER JOIN (
                    SELECT chat_id, MAX(timestamp) AS max_ts
                    FROM messages
                    WHERE chat_id IS NOT NULL AND chat_id != ''
                    GROUP BY chat_id
                ) t
                ON m.chat_id = t.chat_id AND m.timestamp = t.max_ts
                ORDER BY m.timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            result = []
            for chat_id, item_id, last_message, last_timestamp in rows:
                result.append(
                    {
                        "chat_id": chat_id,
                        "item_id": item_id,
                        "last_message": last_message,
                        "last_timestamp": last_timestamp,
                        "bargain_count": self.get_bargain_count_by_chat(chat_id),
                    }
                )
            return result
        except Exception as e:
            logger.error(f"获取会话列表时出错: {e}")
            return []
        finally:
            conn.close()

    def get_messages_by_chat(self, chat_id, limit=200):
        """获取指定会话消息（按时间升序）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT user_id, item_id, role, content, timestamp, chat_id
                FROM messages
                WHERE chat_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (chat_id, limit),
            )
            rows = cursor.fetchall()
            return [
                {
                    "user_id": user_id,
                    "item_id": item_id,
                    "role": role,
                    "content": content,
                    "timestamp": timestamp,
                    "chat_id": c_id,
                }
                for user_id, item_id, role, content, timestamp, c_id in rows
            ]
        except Exception as e:
            logger.error(f"获取会话消息时出错: {e}")
            return []
        finally:
            conn.close()

    def get_last_user_id_by_chat(self, chat_id):
        """获取指定会话最新用户侧发送者ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT user_id
                FROM messages
                WHERE chat_id = ? AND role = 'user'
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (chat_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"获取会话用户ID时出错: {e}")
            return None
        finally:
            conn.close()

    def get_last_item_id_by_chat(self, chat_id):
        """获取指定会话最新商品ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT item_id
                FROM messages
                WHERE chat_id = ? AND item_id IS NOT NULL AND item_id != ''
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (chat_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"获取会话商品ID时出错: {e}")
            return None
        finally:
            conn.close()

    def get_user_message_count_by_chat(self, chat_id):
        """获取会话中用户消息数量"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT COUNT(1)
                FROM messages
                WHERE chat_id = ? AND role = 'user'
                """,
                (chat_id,),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            logger.error(f"获取会话用户消息数时出错: {e}")
            return 0
        finally:
            conn.close()

    def has_triggered_rule(self, chat_id, rule_id):
        """判断某会话是否已触发过指定规则"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT 1 FROM chat_trigger_rules
                WHERE chat_id = ? AND rule_id = ?
                LIMIT 1
                """,
                (chat_id, rule_id),
            )
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"查询触发规则状态时出错: {e}")
            return False
        finally:
            conn.close()

    def mark_triggered_rule(self, chat_id, rule_id):
        """标记某会话已触发规则"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT OR IGNORE INTO chat_trigger_rules (chat_id, rule_id, triggered_at)
                VALUES (?, ?, ?)
                """,
                (chat_id, rule_id, datetime.now().isoformat()),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"标记触发规则时出错: {e}")
            conn.rollback()
        finally:
            conn.close()