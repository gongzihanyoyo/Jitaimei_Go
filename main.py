#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jitaimei Go - 轻量短链接服务
https://github.com/gongzihanyoyo/Jitaimei_Go
"""

import json
import os
import random
import string
import threading
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime

# ------------------ 配置项 ------------------
LOCAL_PORT = 25001          # 若缺失或为空则默认25001
ID_LENGTH_MIN = 5           # 短链 ID 的最小长度
ID_LENGTH_MAX = 10          # 短链 ID 的最大长度
ADMIN_TOKEN = "admin123"    # 管理面板访问令牌，请修改为强密码

# 站点信息（预留冗余接口）
SITE_DOMAIN = "go.jitaimei.top"
SITE_NAME = "Jitaimei Go"

# 文件路径，通常无需修改
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")
BLACKLIST_DIR = os.path.join(BASE_DIR, "blacklist")
BLACKLIST_FILE = os.path.join(BLACKLIST_DIR, "domain.txt")
WEB_DIR = os.path.join(BASE_DIR, "web")

# 必须存在的 HTML 文件，缺失将无法运行，请勿修改
REQUIRED_HTML = ["index.html", "go.html", "error.html", "admin.html"]


# ------------------ 工具函数 ------------------
def ensure_directories_and_files():
    """确保必要目录和文件存在，缺失则创建或提示退出"""
    if not os.path.exists(BLACKLIST_DIR):
        os.makedirs(BLACKLIST_DIR)
        print("[提示] 目录 blacklist 不存在，已自动创建")

    if not os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        print("[提示] 文件 blacklist/domain.txt 不存在，已创建空域名黑名单列表")

    if not os.path.exists(WEB_DIR):
        print("[错误] web 文件夹不存在，请创建并放入必需文件")
        exit(1)
    for html_file in REQUIRED_HTML:
        file_path = os.path.join(WEB_DIR, html_file)
        if not os.path.exists(file_path):
            print(f"[错误] 缺少文件 web/{html_file}，程序退出")
            exit(1)

    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        print("[提示] data.json 不存在，已创建空数据库")

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

def load_blacklist():
    try:
        with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def is_domain_blocked(url, blacklist):
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    hostname = hostname.lower()
    for pattern in blacklist:
        pattern = pattern.lower()
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
            print(f"[清理] 已删除过期短链接: {', '.join(expired_ids)}")
    return data

def periodic_cleanup(interval=3600):
    while True:
        time.sleep(interval)
        clean_expired_links()

# ------------------ HTTP 服务器 ------------------
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query = urllib.parse.parse_qs(parsed_path.query)

        try:
            if path == "/":
                self.serve_static("index.html")
            elif path == "/go":
                self.serve_static("go.html")
            elif path == "/error":
                self.serve_static("error.html")
            elif path == "/admin":
                self.serve_static("admin.html")
            elif path == "/api/v1/create":
                self.api_create(query)
            elif path == "/api/v1/go":
                self.api_go(query)
            elif path == "/api/v1/id_length_limit":
                self.send_json({"code": 200, "min": ID_LENGTH_MIN, "max": ID_LENGTH_MAX})
            elif path == "/api/v1/site_domain":
                self.send_json({"code": 200, "domain": SITE_DOMAIN})
            elif path == "/api/v1/site_name":
                self.send_json({"code": 200, "name": SITE_NAME})
            # ---------- 管理 API ----------
            elif path == "/api/v1/admin_login":
                self.api_admin_login(query)
            elif path == "/api/v1/admin_data":
                self.api_admin_data(query)
            elif path == "/api/v1/admin_del":
                self.api_admin_del(query)
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

    # ------------------ Token 验证 ------------------
    def _check_token(self, query):
        """验证管理 token，成功返回 True，否则返回 False 并发送错误响应"""
        token = query.get("token", [None])[0]
        if not ADMIN_TOKEN:   # 没有配置 token 一律拒绝
            self.send_json({"code": -1, "why": "tokenError"})
            return False
        if token != ADMIN_TOKEN:
            self.send_json({"code": -1, "why": "tokenError"})
            return False
        return True

    # ------------------ 核心业务 ------------------
    def _generate_random_id(self):
        """生成一个随机且不重复的短链接 ID"""
        chars = string.ascii_letters + string.digits
        data = load_data()
        for _ in range(100):
            length = random.randint(ID_LENGTH_MIN, ID_LENGTH_MAX)
            new_id = ''.join(random.choices(chars, k=length))
            if new_id not in data:
                return new_id
        return None

    def api_create(self, query):
        sid = query.get("id", [None])[0]
        if not sid:
            return self.send_json({"code": -1, "why": "idNotFound"})

        raw_link = query.get("link", [None])[0]
        if not raw_link:
            return self.send_json({"code": -1, "why": "unknow"})

        link = raw_link.strip('"')

        blacklist = load_blacklist()
        if is_domain_blocked(link, blacklist):
            return self.send_json({"code": -1, "why": "domainBlocked"})

        deadline_raw = query.get("deadlinedate", [None])[0]
        deadline = parse_deadline(deadline_raw)

        data = load_data()

        # 随机生成模式 (id=-1)
        if sid == "-1":
            for existing_id, info in data.items():
                if isinstance(info, dict):
                    if info.get("link") == link and info.get("deadlinedate") == deadline:
                        return self.send_json({"code": 200, "id": existing_id})

            new_id = self._generate_random_id()
            if new_id is None:
                return self.send_json({"code": -1, "why": "unknow"})

            data[new_id] = {"link": link, "deadlinedate": deadline}
            save_data(data)
            return self.send_json({"code": 200, "id": new_id})

        # 自定义 ID 模式
        if sid in data:
            return self.send_json({"code": -1, "why": "idAlreadyExists"})

        data[sid] = {"link": link, "deadlinedate": deadline}
        save_data(data)
        return self.send_json({"code": 200, "id": sid})

    def api_go(self, query):
        sid = query.get("id", [None])[0]
        if not sid:
            return self.send_json({"code": -1, "why": "notFound"})

        data = load_data()
        info = data.get(sid)
        if not info or not isinstance(info, dict):
            return self.send_json({"code": -1, "why": "notFound"})

        if is_expired(info.get("deadlinedate", "-1")):
            clean_expired_links()
            return self.send_json({"code": -1, "why": "notFound"})

        self.send_json({"code": 200, "link": info["link"]})

    # ------------------ 管理 API ------------------
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
        data = load_data()
        if sid not in data:
            self.send_json({"code": -1, "why": "notFound"})
            return
        del data[sid]
        save_data(data)
        self.send_json({"code": 200})

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
        # 保存到文件
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(new_list, f, ensure_ascii=False, indent=2)
        self.send_json({"code": 200})

    def log_message(self, format, *args):
        pass

# ------------------ 主程序 ------------------
def main():
    port = LOCAL_PORT if LOCAL_PORT else 25001
    if not LOCAL_PORT:
        print(f"[提示] LOCAL_PORT 未配置，使用默认端口 25001")

    print("管理面板登录token：", ADMIN_TOKEN)

    ensure_directories_and_files()
    clean_expired_links()
    threading.Thread(target=periodic_cleanup, args=(3600,), daemon=True).start()

    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, RequestHandler)
    print(f"Jitaimei Go 短链接服务已启动，监听端口 {port}")
    print(f"本机访问地址: http://127.0.0.1:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        httpd.server_close()

if __name__ == "__main__":
    main()
