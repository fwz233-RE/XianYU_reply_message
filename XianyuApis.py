import time
import os
import re
import sys
import hashlib
from typing import Callable, Optional

import requests
from loguru import logger
from project_paths import get_env_file_path
from utils.xianyu_utils import generate_sign, trans_cookies


COOKIE_DOMAIN_PRIORITY_BY_NAME = {
    "_m_h5_tk": [
        "h5api.m.goofish.com",
        "acs.m.goofish.com",
        "www.goofish.com",
        "goofish.com",
        "passport.goofish.com",
    ],
    "_m_h5_tk_enc": [
        "h5api.m.goofish.com",
        "acs.m.goofish.com",
        "www.goofish.com",
        "goofish.com",
        "passport.goofish.com",
    ],
    "XSRF-TOKEN": [
        "passport.goofish.com",
        "www.goofish.com",
        "goofish.com",
        "h5api.m.goofish.com",
    ],
    "cookie2": [
        "goofish.com",
        "www.goofish.com",
        "passport.goofish.com",
        "h5api.m.goofish.com",
    ],
    "unb": [
        "goofish.com",
        "www.goofish.com",
        "passport.goofish.com",
        "h5api.m.goofish.com",
    ],
    "cna": [
        "goofish.com",
        "www.goofish.com",
        "passport.goofish.com",
        "h5api.m.goofish.com",
    ],
}

DEFAULT_COOKIE_DOMAIN_PRIORITY = [
    "h5api.m.goofish.com",
    "acs.m.goofish.com",
    "www.goofish.com",
    "passport.goofish.com",
    "goofish.com",
]


class XianyuApis:
    def __init__(self):
        self.url = 'https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/'
        self.session = requests.Session()
        self.cookie_refresh_callback: Optional[Callable[[str, str], Optional[str]]] = None
        self.cookie_updated_callback: Optional[Callable[[str], None]] = None
        self.status_callback: Optional[Callable[[str, dict], None]] = None
        self.allow_manual_cookie_input = True
        self.waiting_cookie_refresh_hash = ""
        self.waiting_cookie_refresh_reason = ""
        self.session.headers.update({
            'accept': 'application/json',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'origin': 'https://www.goofish.com',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://www.goofish.com/',
            'sec-ch-ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        })

    def set_cookie_refresh_callback(self, callback: Callable[[str, str], Optional[str]]) -> None:
        """设置Cookie刷新回调（用于风控时从外部桥接获取新Cookie）"""
        self.cookie_refresh_callback = callback

    def set_cookie_updated_callback(self, callback: Callable[[str], None]) -> None:
        """设置Cookie更新后的通知回调"""
        self.cookie_updated_callback = callback

    def set_status_callback(self, callback: Callable[[str, dict], None]) -> None:
        """设置运行时状态回调"""
        self.status_callback = callback

    def get_cookie_string(self) -> str:
        seen = {}
        for cookie in self.session.cookies:
            current = seen.get(cookie.name)
            if current is None or self._cookie_score(cookie) < self._cookie_score(current):
                seen[cookie.name] = cookie
        return '; '.join([f"{name}={seen[name].value}" for name in sorted(seen.keys())])

    def _cookie_score(self, cookie) -> tuple:
        name = str(getattr(cookie, "name", "") or "")
        domain = str(getattr(cookie, "domain", "") or "").lstrip(".").lower()
        path = str(getattr(cookie, "path", "") or "/")
        host_only = 0 if not getattr(cookie, "domain_initial_dot", False) else 1
        preferred_domains = COOKIE_DOMAIN_PRIORITY_BY_NAME.get(name, DEFAULT_COOKIE_DOMAIN_PRIORITY)
        try:
            domain_rank = preferred_domains.index(domain)
        except ValueError:
            domain_rank = len(preferred_domains)
        root_path = 0 if path == "/" else 1
        return (domain_rank, host_only, root_path, len(path))

    def get_cookie_value(self, name: str, default: str = "") -> str:
        matched = [cookie for cookie in self.session.cookies if cookie.name == name and cookie.value]
        if not matched:
            return default
        matched.sort(key=self._cookie_score)
        return matched[0].value or default

    def _cookie_signature(self, cookie_str: str) -> str:
        cookies = trans_cookies(cookie_str)
        if not cookies:
            return ""
        normalized = "; ".join(f"{key}={cookies[key]}" for key in sorted(cookies.keys()))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _mark_waiting_cookie_refresh(self, reason: str, current_cookie: str) -> None:
        self.waiting_cookie_refresh_reason = reason
        self.waiting_cookie_refresh_hash = self._cookie_signature(current_cookie)

    def _clear_waiting_cookie_refresh(self, new_cookie_str: str = "") -> None:
        if not self.waiting_cookie_refresh_hash:
            return
        new_hash = self._cookie_signature(new_cookie_str or self.get_cookie_string())
        if new_hash and new_hash != self.waiting_cookie_refresh_hash:
            self.waiting_cookie_refresh_hash = ""
            self.waiting_cookie_refresh_reason = ""

    def set_cookies_from_string(self, cookie_str: str, persist_env: bool = True) -> bool:
        """从Cookie字符串更新session，可选写回.env"""
        cookies = trans_cookies(cookie_str)
        if not cookies.get('unb'):
            return False

        self.session.cookies.clear()
        for key, value in cookies.items():
            self.session.cookies.set(key, value, domain='.goofish.com')

        if persist_env:
            self.update_env_cookies()

        self._clear_waiting_cookie_refresh(cookie_str)

        if self.cookie_updated_callback:
            try:
                self.cookie_updated_callback(cookie_str)
            except Exception as e:
                logger.warning(f"Cookie更新回调失败: {e}")
        return True
        
    def clear_duplicate_cookies(self):
        """清理重复的cookies"""
        new_jar = requests.cookies.RequestsCookieJar()
        best_by_name = {}
        for cookie in self.session.cookies:
            current = best_by_name.get(cookie.name)
            if current is None or self._cookie_score(cookie) < self._cookie_score(current):
                best_by_name[cookie.name] = cookie

        for cookie in best_by_name.values():
            new_jar.set_cookie(cookie)

        self.session.cookies = new_jar
        self.update_env_cookies()
        
    def update_env_cookies(self):
        """更新.env文件中的COOKIES_STR"""
        try:
            # 写回去重后的Cookie，避免把不同域的同名Cookie原样落盘
            cookie_str = self.get_cookie_string()
            
            # 读取.env文件
            env_path = get_env_file_path()
            if not env_path.exists():
                logger.warning(".env文件不存在，无法更新COOKIES_STR")
                return
                
            with open(env_path, 'r', encoding='utf-8') as f:
                env_content = f.read()
                
            # 使用正则表达式替换COOKIES_STR的值
            if 'COOKIES_STR=' in env_content:
                new_env_content = re.sub(
                    r'COOKIES_STR=.*', 
                    f'COOKIES_STR={cookie_str}',
                    env_content
                )
                
                # 写回.env文件
                with open(env_path, 'w', encoding='utf-8') as f:
                    f.write(new_env_content)
                    
                logger.debug("已更新.env文件中的COOKIES_STR")
            else:
                logger.warning(".env文件中未找到COOKIES_STR配置项")
        except Exception as e:
            logger.warning(f"更新.env文件失败: {str(e)}")
        
    def hasLogin(self, retry_count=0):
        """调用hasLogin.do接口进行登录状态检查"""
        if retry_count >= 2:
            logger.error("Login检查失败，重试次数过多")
            return False
            
        try:
            url = 'https://passport.goofish.com/newlogin/hasLogin.do'
            params = {
                'appName': 'xianyu',
                'fromSite': '77'
            }
            data = {
                'hid': self.get_cookie_value('unb', ''),
                'ltl': 'true',
                'appName': 'xianyu',
                'appEntrance': 'web',
                '_csrf_token': self.get_cookie_value('XSRF-TOKEN', ''),
                'umidToken': '',
                'hsiz': self.get_cookie_value('cookie2', ''),
                'bizParams': 'taobaoBizLoginFrom=web',
                'mainPage': 'false',
                'isMobile': 'false',
                'lang': 'zh_CN',
                'returnUrl': '',
                'fromSite': '77',
                'isIframe': 'true',
                'documentReferer': 'https://www.goofish.com/',
                'defaultView': 'hasLogin',
                'umidTag': 'SERVER',
                'deviceId': self.get_cookie_value('cna', '')
            }
            
            response = self.session.post(url, params=params, data=data)
            res_json = response.json()
            
            if res_json.get('content', {}).get('success'):
                logger.debug("Login成功")
                # 清理和更新cookies
                self.clear_duplicate_cookies()
                return True
            else:
                logger.warning(f"Login失败: {res_json}")
                time.sleep(0.5)
                return self.hasLogin(retry_count + 1)
                
        except Exception as e:
            logger.error(f"Login请求异常: {str(e)}")
            time.sleep(0.5)
            return self.hasLogin(retry_count + 1)

    def _wait_for_fresh_cookie(self, reason: str, current_cookie: str) -> Optional[str]:
        if self.status_callback:
            self.status_callback("risk_control_waiting_cookie", {"reason": reason})

        new_cookie_str = None
        if self.cookie_refresh_callback:
            try:
                new_cookie_str = self.cookie_refresh_callback(reason, current_cookie)
            except Exception as e:
                logger.warning(f"插件Cookie刷新回调失败: {e}")

        if not new_cookie_str and self.allow_manual_cookie_input:
            print("\n" + "=" * 50)
            new_cookie_str = input("请输入新的Cookie字符串 (复制浏览器中的完整cookie，直接回车则退出程序): ").strip()
            print("=" * 50 + "\n")

        if new_cookie_str and self.set_cookies_from_string(new_cookie_str, persist_env=True):
            logger.success("✅ Cookie已更新，正在尝试重连...")
            if self.status_callback:
                self.status_callback("risk_control_cookie_applied", {"reason": reason})
            return new_cookie_str

        return None

    def _handle_cookie_refresh_wait(self, reason: str, current_cookie: str):
        new_cookie_str = self._wait_for_fresh_cookie(reason, current_cookie)
        if new_cookie_str:
            return new_cookie_str
        if not self.allow_manual_cookie_input:
            self._mark_waiting_cookie_refresh(reason, current_cookie)
            logger.warning("UI模式下不回退控制台输入，继续等待插件推送新Cookie")
            return {"ret": ["WAITING_FOR_COOKIE_REFRESH"], "reason": reason}
        logger.info("未获取到有效Cookie，程序退出")
        sys.exit(1)

    def get_token(self, device_id, retry_count=0):
        current_cookie = self.get_cookie_string()
        if self.waiting_cookie_refresh_hash:
            current_hash = self._cookie_signature(current_cookie)
            if current_hash and current_hash == self.waiting_cookie_refresh_hash:
                return {"ret": ["WAITING_FOR_COOKIE_REFRESH"], "reason": self.waiting_cookie_refresh_reason or "risk_control"}
            self._clear_waiting_cookie_refresh(current_cookie)

        if retry_count >= 2:  # 最多重试3次
            logger.warning("获取token失败，尝试恢复会话")
            refreshed = self._handle_cookie_refresh_wait("session_expired", current_cookie)
            if isinstance(refreshed, dict):
                return refreshed
            if refreshed:
                logger.info("已获取新的Cookie，重新尝试获取token")
                return self.get_token(device_id, 0)

            logger.warning("等待Cookie刷新未成功，尝试重新登陆")
            # 尝试通过hasLogin重新登录
            if self.hasLogin():
                logger.info("重新登录成功，重新尝试获取token")
                return self.get_token(device_id, 0)  # 重置重试次数
            else:
                logger.error("重新登录失败，Cookie已失效")
                logger.error("🔴 程序即将退出，请更新.env文件中的COOKIES_STR后重新启动")
                sys.exit(1)  # 直接退出程序
            
        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idlemessage.pc.login.token',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        data_val = '{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"' + device_id + '"}'
        data = {
            'data': data_val,
        }
        
        # 简单获取token，信任cookies已清理干净
        token = self.get_cookie_value('_m_h5_tk', '').split('_')[0]
        
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign
        
        try:
            response = self.session.post('https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/', params=params, data=data)
            res_json = response.json()
            
            if isinstance(res_json, dict):
                ret_value = res_json.get('ret', [])
                # 检查ret是否包含成功信息
                if not any('SUCCESS::调用成功' in ret for ret in ret_value):
                    # 检测风控/限流错误
                    error_msg = str(ret_value)
                    if 'RGV587_ERROR' in error_msg or '被挤爆啦' in error_msg:
                        logger.error(f"❌ 触发风控: {ret_value}")
                        logger.error("🔴 系统触发风控，将尝试等待插件推送新Cookie；失败后回退手动输入")
                        refreshed = self._handle_cookie_refresh_wait("risk_control", current_cookie)
                        if isinstance(refreshed, str):
                            return self.get_token(device_id, 0)
                        return refreshed

                    if 'FAIL_SYS_SESSION_EXPIRED' in error_msg or 'FAIL_SYS_USER_VALIDATE' in error_msg:
                        logger.warning(f"检测到会话失效或验证中断: {ret_value}")
                        if 'Set-Cookie' in response.headers:
                            logger.debug("检测到Set-Cookie，更新cookie")
                            self.clear_duplicate_cookies()
                            current_cookie = self.get_cookie_string()
                        if (not self.allow_manual_cookie_input) or ('FAIL_SYS_USER_VALIDATE' in error_msg) or retry_count >= 1:
                            refreshed = self._handle_cookie_refresh_wait("slider_verification", current_cookie)
                            if isinstance(refreshed, str):
                                return self.get_token(device_id, 0)
                            return refreshed
                        time.sleep(0.5)
                        return self.get_token(device_id, retry_count + 1)

                    logger.warning(f"Token API调用失败，错误信息: {ret_value}")
                    # 处理响应中的Set-Cookie
                    if 'Set-Cookie' in response.headers:
                        logger.debug("检测到Set-Cookie，更新cookie")  # 降级为DEBUG并简化
                        self.clear_duplicate_cookies()
                    time.sleep(0.5)
                    return self.get_token(device_id, retry_count + 1)
                else:
                    logger.info("Token获取成功")
                    if self.status_callback:
                        self.status_callback("token_refresh_success", {})
                    return res_json
            else:
                logger.error(f"Token API返回格式异常: {res_json}")
                return self.get_token(device_id, retry_count + 1)
                
        except Exception as e:
            logger.error(f"Token API请求异常: {str(e)}")
            time.sleep(0.5)
            return self.get_token(device_id, retry_count + 1)

    def get_item_info(self, item_id, retry_count=0):
        """获取商品信息，自动处理token失效的情况"""
        if retry_count >= 3:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}
            
        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idle.pc.detail',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        
        data_val = '{"itemId":"' + item_id + '"}'
        data = {
            'data': data_val,
        }
        
        # 简单获取token，信任cookies已清理干净
        token = self.get_cookie_value('_m_h5_tk', '').split('_')[0]
        
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign
        
        try:
            response = self.session.post(
                'https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/', 
                params=params, 
                data=data
            )
            
            res_json = response.json()
            # 检查返回状态
            if isinstance(res_json, dict):
                ret_value = res_json.get('ret', [])
                # 检查ret是否包含成功信息
                if not any('SUCCESS::调用成功' in ret for ret in ret_value):
                    logger.warning(f"商品信息API调用失败，错误信息: {ret_value}")
                    # 处理响应中的Set-Cookie
                    if 'Set-Cookie' in response.headers:
                        logger.debug("检测到Set-Cookie，更新cookie")
                        self.clear_duplicate_cookies()
                    time.sleep(0.5)
                    return self.get_item_info(item_id, retry_count + 1)
                else:
                    logger.debug(f"商品信息获取成功: {item_id}")
                    return res_json
            else:
                logger.error(f"商品信息API返回格式异常: {res_json}")
                return self.get_item_info(item_id, retry_count + 1)
                
        except Exception as e:
            logger.error(f"商品信息API请求异常: {str(e)}")
            time.sleep(0.5)
            return self.get_item_info(item_id, retry_count + 1)
