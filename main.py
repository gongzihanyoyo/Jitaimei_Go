#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jitaimei Go - 轻量短链接服务
https://github.com/gongzihanyoyo/Jitaimei_Go
©2025-2026 Jitaimei Studio™
"""

import json
import os
import random
import re
import string
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime

# ------------------ 加载外部配置 ------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

try:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"[错误] 配置文件 config.json 不存在或格式错误: {e}")
    sys.exit(1)

# 提取必要配置
LOCAL_PORT = config.get("LOCAL_PORT", 25001)
ID_LENGTH_MIN = config.get("ID_LENGTH_MIN")
ID_LENGTH_MAX = config.get("ID_LENGTH_MAX")
ADMIN_TOKEN = config.get("ADMIN_TOKEN")
ENABLE_SUPER_LINK = config.get("ENABLE_SUPER_LINK", True)
ENABLE_BATCH = config.get("ENABLE_BATCH", True)        # 批量创建，默认启用

# 检查必要配置
if ID_LENGTH_MIN is None or ID_LENGTH_MAX is None or ADMIN_TOKEN is None:
    print("[错误] config.json 缺少必要配置项 (ID_LENGTH_MIN, ID_LENGTH_MAX, ADMIN_TOKEN)")
    sys.exit(1)

# 可容忍缺失的配置
SITE_DOMAIN = config.get("SITE_DOMAIN", "go.jitaimei.top")
SITE_NAME = config.get("SITE_NAME", "Jitaimei Go")

# 文件路径
DATA_FILE = os.path.join(BASE_DIR, "data.json")
DATA_SUPER_FILE = os.path.join(BASE_DIR, "data_super.json")
BLACKLIST_DIR = os.path.join(BASE_DIR, "blacklist")
BLACKLIST_FILE = os.path.join(BLACKLIST_DIR, "domain.txt")
WEB_DIR = os.path.join(BASE_DIR, "web")
MEFRPC_DIR = os.path.join(BASE_DIR, "mefrpc")
MEFRPC_EXEC = os.path.join(MEFRPC_DIR, "mefrpc")
MEFRPC_CONF = os.path.join(MEFRPC_DIR, "frpc.toml")

# 必须存在的 HTML 文件
REQUIRED_HTML = ["index.html", "go.html", "error.html", "admin.html", "super.html", "batch.html"]

# 全局数据锁
data_lock = threading.Lock()
data_super_lock = threading.Lock()

# mefrpc 进程引用
mefrpc_process = None

# ------------------ 工具函数 ------------------
def ensure_directories_and_files():
    """确保必要目录和文件存在，缺失则创建或提示退出"""
    if not os.path.exists(BLACKLIST_DIR):
        os.makedirs(BLACKLIST_DIR)
        print("[提示] 目录 blacklist 不存在，已自动创建")

    if not os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        print("[提示] 文件 blacklist/domain.txt 不存在，已创建空黑名单")

    if not os.path.exists(WEB_DIR):
        print("[错误] web 文件夹不存在，请创建并放入必需文件")
        sys.exit(1)
    for html_file in REQUIRED_HTML:
        file_path = os.path.join(WEB_DIR, html_file)
        if not os.path.exists(file_path):
            print(f"[错误] 缺少文件 web/{html_file}，程序退出")
            sys.exit(1)

    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        print("[提示] data.json 不存在，已创建空数据库")

    if not os.path.exists(DATA_SUPER_FILE):
        with open(DATA_SUPER_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        print("[提示] data_super.json 不存在，已创建空数据库")

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_data(data):
    temp_file = DATA_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, DATA_FILE)

def load_super_data():
    try:
        with open(DATA_SUPER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_super_data(data):
    temp_file = DATA_SUPER_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, DATA_SUPER_FILE)

def load_blacklist():
    try:
        with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def is_domain_blocked(url, blacklist):
    """检查域名是否在黑名单中"""
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    hostname = urllib.parse.unquote(hostname).lower()
    for pattern in blacklist:
        pattern = urllib.parse.unquote(pattern).lower()
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if hostname.endswith(suffix) or hostname == pattern[2:]:
                return True
        else:
            if hostname == pattern:
                return True
    return False

def parse_deadline(date_str):
    if not date_str or date_str == "-1":
        return "-1"
    try:
        datetime.strptime(date_str, "%Y%m%d")
        return date_str
    except ValueError:
        return "-1"

def is_expired(deadline_str):
    if deadline_str == "-1":
        return False
    try:
        deadline = datetime.strptime(deadline_str, "%Y%m%d").date()
        return deadline < datetime.now().date()
    except ValueError:
        return True

def clean_expired_links():
    """清理过期普通短链"""
    with data_lock:
        data = load_data()
        changed = False
        expired_ids = []
        for sid, info in list(data.items()):
            if isinstance(info, dict) and "deadlinedate" in info:
                if is_expired(info["deadlinedate"]):
                    expired_ids.append(sid)
                    del data[sid]
                    changed = True
        if changed:
            save_data(data)
            if expired_ids:
                print(f"[清理] 已删除过期普通短链接: {', '.join(expired_ids)}")

def clean_expired_super_links():
    """清理过期超级短链"""
    with data_super_lock:
        data = load_super_data()
        changed = False
        expired_ids = []
        for sid, info in list(data.items()):
            if isinstance(info, dict) and "deadlinedate" in info:
                if is_expired(info["deadlinedate"]):
                    expired_ids.append(sid)
                    del data[sid]
                    changed = True
        if changed:
            save_super_data(data)
            if expired_ids:
                print(f"[清理] 已删除过期超级短链接: {', '.join(expired_ids)}")

def periodic_cleanup(interval=3600):
    while True:
        time.sleep(interval)
        clean_expired_links()
        clean_expired_super_links()

# ------------------ 链接验证与规范化 ------------------
def validate_and_normalize_link(raw_link):
    link = raw_link.strip().strip('"')
    if not link:
        return None
    if re.match(r'^javascript\s*:', link, re.IGNORECASE):
        return None
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', link):
        if len(link) > len(link.split('://')[0]) + 3:
            return link
        return None
    if '.' in link and ' ' not in link:
        return 'http://' + link
    return None

# ------------------ HTTP 服务器 ------------------
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class RequestHandler(BaseHTTPRequestHandler):

    def get_client_ip(self):
        x_forwarded_for = self.headers.get('X-Forwarded-For')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
            if ip:
                return ip
        x_real_ip = self.headers.get('X-Real-IP')
        if x_real_ip:
            return x_real_ip.strip()
        return self.client_address[0]

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query = urllib.parse.parse_qs(parsed_path.query)

        try:
            if path == "/":
                self.serve_static("index.html")
            elif path == "/go":
                self.serve_static("go.html")
            elif path == "/super":
                self.serve_static("super.html")
            elif path == "/batch":
                self.serve_static("batch.html")
            elif path == "/error":
                self.serve_static("error.html")
            elif path == "/admin":
                self.serve_static("admin.html")
            # ---------- 普通短链 API ----------
            elif path == "/api/v1/create":
                self.api_create(query)
            elif path == "/api/v1/go":
                self.api_go(query)
            # ---------- 超级短链 API ----------
            elif path == "/api/v1/create_super":
                self.api_create_super(query)
            elif path == "/api/v1/go_super":
                self.api_go_super(query)
            elif path == "/api/v1/enable_super_link":
                self.api_enable_super_link()
            # ---------- 批量创建开关查询 ----------
            elif path == "/api/v1/enable_batch":
                self.api_enable_batch()
            # ---------- 通用 API ----------
            elif path == "/api/v1/id_length_limit":
                self.send_json({"code": 200, "min": ID_LENGTH_MIN, "max": ID_LENGTH_MAX})
            elif path == "/api/v1/site_domain":
                self.send_json({"code": 200, "domain": SITE_DOMAIN})
            elif path == "/api/v1/site_name":
                self.send_json({"code": 200, "name": SITE_NAME})
            # ---------- 管理 API（普通短链）----------
            elif path == "/api/v1/admin_login":
                self.api_admin_login(query)
            elif path == "/api/v1/admin_data":
                self.api_admin_data(query)
            elif path == "/api/v1/admin_del":
                self.api_admin_del(query)
            # ---------- 管理 API（超级短链）----------
            elif path == "/api/v1/admin_data_super":
                self.api_admin_data_super(query)
            elif path == "/api/v1/admin_del_super":
                self.api_admin_del_super(query)
            # ---------- 黑名单管理 ----------
            elif path == "/api/v1/admin_blacklist_domain_show":
                self.api_admin_blacklist_show(query)
            elif path == "/api/v1/admin_blacklist_domain_change":
                self.api_admin_blacklist_change(query)
            else:
                self.send_error_response(404, "Not Found")
        except Exception as e:
            print(f"[错误] 处理请求时发生异常: {e}")
            self.send_error_response(500, "Internal Server Error")

    def serve_static(self, filename):
        filepath = os.path.join(WEB_DIR, filename)
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error_response(404, "File Not Found")

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def send_error_response(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"code": -1, "why": message}).encode("utf-8"))

    def _check_token(self, query):
        token = query.get("token", [None])[0]
        if not ADMIN_TOKEN:
            self.send_json({"code": -1, "why": "tokenError"})
            return False
        if token != ADMIN_TOKEN:
            self.send_json({"code": -1, "why": "tokenError"})
            return False
        return True

    # ------------------ 开关查询 ------------------
    def api_enable_super_link(self):
        self.send_json({"code": 200, "enable": bool(ENABLE_SUPER_LINK)})

    def api_enable_batch(self):
        self.send_json({"code": 200, "enable": bool(ENABLE_BATCH)})

    # ------------------ 普通短链创建 ------------------
    def api_create(self, query):
        sid = query.get("id", [None])[0]
        if not sid:
            return self.send_json({"code": -1, "why": "idNotFound"})

        raw_link = query.get("link", [None])[0]
        if not raw_link:
            return self.send_json({"code": -1, "why": "unknow"})

        link = validate_and_normalize_link(raw_link)
        if link is None:
            return self.send_json({"code": -1, "why": "linkError"})

        blacklist = load_blacklist()
        if is_domain_blocked(link, blacklist):
            return self.send_json({"code": -1, "why": "domainBlocked"})

        deadline_raw = query.get("deadlinedate", [None])[0]
        deadline = parse_deadline(deadline_raw)
        ip = self.get_client_ip()
        createtime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with data_lock:
            data = load_data()
            if sid == "-1":
                for existing_id, info in data.items():
                    if isinstance(info, dict):
                        if info.get("link") == link and info.get("deadlinedate") == deadline:
                            return self.send_json({"code": 200, "id": existing_id})

                chars = string.ascii_letters + string.digits
                new_id = None
                for _ in range(100):
                    length = random.randint(ID_LENGTH_MIN, ID_LENGTH_MAX)
                    candidate = ''.join(random.choices(chars, k=length))
                    if candidate not in data:
                        new_id = candidate
                        break
                if new_id is None:
                    return self.send_json({"code": -1, "why": "unknow"})

                data[new_id] = {
                    "link": link,
                    "deadlinedate": deadline,
                    "ip": ip,
                    "createtime": createtime,
                    "view": 0
                }
                save_data(data)
                return self.send_json({"code": 200, "id": new_id})

            if sid in data:
                return self.send_json({"code": -1, "why": "idAlreadyExists"})

            data[sid] = {
                "link": link,
                "deadlinedate": deadline,
                "ip": ip,
                "createtime": createtime,
                "view": 0
            }
            save_data(data)
            return self.send_json({"code": 200, "id": sid})

    def api_go(self, query):
        sid = query.get("id", [None])[0]
        if not sid:
            return self.send_json({"code": -1, "why": "notFound"})
        with data_lock:
            data = load_data()
            info = data.get(sid)
            if not info or not isinstance(info, dict):
                return self.send_json({"code": -1, "why": "notFound"})
            if is_expired(info.get("deadlinedate", "-1")):
                del data[sid]
                save_data(data)
                return self.send_json({"code": -1, "why": "notFound"})
            info["view"] = info.get("view", 0) + 1
            data[sid] = info
            save_data(data)
            link = info["link"]
        self.send_json({"code": 200, "link": link})

    # ------------------ 超级短链创建 ------------------
    def api_create_super(self, query):
        if not ENABLE_SUPER_LINK:
            return self.send_json({"code": -1, "why": "superLinkDisabled"})

        sid = query.get("id", [None])[0]
        if not sid:
            return self.send_json({"code": -1, "why": "idNotFound"})

        raw_link = query.get("link", [None])[0]
        if not raw_link:
            return self.send_json({"code": -1, "why": "unknow"})

        link = validate_and_normalize_link(raw_link)
        if link is None:
            return self.send_json({"code": -1, "why": "linkError"})

        blacklist = load_blacklist()
        if is_domain_blocked(link, blacklist):
            return self.send_json({"code": -1, "why": "domainBlocked"})

        deadline_raw = query.get("deadlinedate", [None])[0]
        deadline = parse_deadline(deadline_raw)
        ip = self.get_client_ip()
        createtime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with data_super_lock:
            data = load_super_data()
            if sid == "-1":
                for existing_id, info in data.items():
                    if isinstance(info, dict):
                        if info.get("link") == link and info.get("deadlinedate") == deadline:
                            return self.send_json({"code": 200, "id": existing_id})

                chars = string.ascii_letters + string.digits
                new_id = None
                for _ in range(100):
                    length = random.randint(ID_LENGTH_MIN, ID_LENGTH_MAX)
                    candidate = ''.join(random.choices(chars, k=length))
                    if candidate not in data:
                        new_id = candidate
                        break
                if new_id is None:
                    return self.send_json({"code": -1, "why": "unknow"})

                data[new_id] = {
                    "link": link,
                    "deadlinedate": deadline,
                    "ip": ip,
                    "createtime": createtime,
                    "view": 0
                }
                save_super_data(data)
                return self.send_json({"code": 200, "id": new_id})

            if sid in data:
                return self.send_json({"code": -1, "why": "idAlreadyExists"})

            data[sid] = {
                "link": link,
                "deadlinedate": deadline,
                "ip": ip,
                "createtime": createtime,
                "view": 0
            }
            save_super_data(data)
            return self.send_json({"code": 200, "id": sid})

    def api_go_super(self, query):
        if not ENABLE_SUPER_LINK:
            return self.send_json({"code": -1, "why": "superLinkDisabled"})

        sid = query.get("id", [None])[0]
        if not sid:
            return self.send_json({"code": -1, "why": "notFound"})

        with data_super_lock:
            data = load_super_data()
            info = data.get(sid)
            if not info or not isinstance(info, dict):
                return self.send_json({"code": -1, "why": "notFound"})
            if is_expired(info.get("deadlinedate", "-1")):
                del data[sid]
                save_super_data(data)
                return self.send_json({"code": -1, "why": "notFound"})
            info["view"] = info.get("view", 0) + 1
            data[sid] = info
            save_super_data(data)
            link = info["link"]
        self.send_json({"code": 200, "link": link})

    # ------------------ 管理 API（普通短链）------------------
    def api_admin_login(self, query):
        token = query.get("token", [None])[0]
        if not ADMIN_TOKEN:
            return self.send_json({"code": -1, "why": "tokenError"})
        if token == ADMIN_TOKEN:
            self.send_json({"code": 200})
        else:
            self.send_json({"code": -1, "why": "tokenError"})

    def api_admin_data(self, query):
        if not self._check_token(query):
            return
        data = load_data()
        self.send_json({"code": 200, "data": data})

    def api_admin_del(self, query):
        if not self._check_token(query):
            return
        sid = query.get("id", [None])[0]
        if not sid:
            self.send_json({"code": -1, "why": "notFound"})
            return
        with data_lock:
            data = load_data()
            if sid not in data:
                self.send_json({"code": -1, "why": "notFound"})
                return
            del data[sid]
            save_data(data)
        self.send_json({"code": 200})

    # ------------------ 管理 API（超级短链）------------------
    def api_admin_data_super(self, query):
        if not self._check_token(query):
            return
        data = load_super_data()
        self.send_json({"code": 200, "data": data})

    def api_admin_del_super(self, query):
        if not self._check_token(query):
            return
        sid = query.get("id", [None])[0]
        if not sid:
            self.send_json({"code": -1, "why": "notFound"})
            return
        with data_super_lock:
            data = load_super_data()
            if sid not in data:
                self.send_json({"code": -1, "why": "notFound"})
                return
            del data[sid]
            save_super_data(data)
        self.send_json({"code": 200})

    # ------------------ 黑名单管理 ------------------
    def api_admin_blacklist_show(self, query):
        if not self._check_token(query):
            return
        blacklist = load_blacklist()
        self.send_json({"code": 200, "blacklist_domain": blacklist})

    def api_admin_blacklist_change(self, query):
        if not self._check_token(query):
            return
        new_raw = query.get("new", [None])[0]
        if new_raw is None:
            self.send_json({"code": -1, "why": "formatError"})
            return
        try:
            new_list = json.loads(new_raw)
            if not isinstance(new_list, list):
                raise ValueError("Not a list")
        except Exception:
            self.send_json({"code": -1, "why": "formatError"})
            return
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(new_list, f, ensure_ascii=False, indent=2)
        self.send_json({"code": 200})

    def log_message(self, format, *args):
        pass

def main():
    port = LOCAL_PORT if LOCAL_PORT else 25001
    if not LOCAL_PORT:
        print("[提示] LOCAL_PORT 未配置，使用默认端口 25001")

    ensure_directories_and_files()
    clean_expired_links()
    clean_expired_super_links()
    threading.Thread(target=periodic_cleanup, args=(3600,), daemon=True).start()

    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, RequestHandler)
    print(f"{SITE_NAME} 短链接服务已启动，监听端口 {port}")
    print(f"本机访问地址: http://127.0.0.1:{port}/")
    print("管理面板访问令牌：", ADMIN_TOKEN)
    if ENABLE_SUPER_LINK:
        print("超级短链功能已启用")
    else:
        print("超级短链功能已禁用")
    if ENABLE_BATCH:
        print("批量创建功能已启用")
    else:
        print("批量创建功能已禁用")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        httpd.server_close()
        sys.exit(0)

if __name__ == "__main__":
    main()
