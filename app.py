# -*- coding: utf-8 -*-
"""
每日任务清单 · 后端 (v0.1 MVP)

技术约束（见 docs/技术方案.md）：
- 仅使用 Python 3 标准库，零第三方依赖
- 监听 127.0.0.1，本机单机使用，不做登录鉴权
- 数据存本地 tasks.json，读写均显式 UTF-8，原子写
- 业务逻辑（存储层）与 HTTP 处理分离，便于单测
"""
import datetime
import html
import json
import sys
import logging
import os
import re
import shutil
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 配置（模块级，便于测试时猴子补丁）
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_FILE = os.path.join(BASE_DIR, "tasks.json")
PID_FILE = os.path.join(BASE_DIR, "server.pid")
LOG_FILE = os.path.join(BASE_DIR, "app.log")
URL_FILE = os.path.join(BASE_DIR, "app.url")
BACKUP_PREFIX = "tasks.backup."
MAX_CONTENT_LEN = 500
DEFAULT_PORT = 8000
HOST = "127.0.0.1"

CONFIG = {
    "data_file": DATA_FILE,
    "pid_file": PID_FILE,
    "log_file": LOG_FILE,
}

LOGGER = logging.getLogger("todolist")


# ===========================================================================
# 存储层（纯函数，不依赖 socket，便于单测）
# ===========================================================================
def now_iso():
    """当前时间，ISO 格式，为后续「每日/历史」版本铺路。"""
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str():
    """今天日期，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def load_tasks(path=None):
    """读取任务列表；文件不存在或损坏时返回空列表。"""
    path = path or CONFIG["data_file"]
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        LOGGER.warning("读取 %s 失败，按空清单处理", path)
        return []
    if not isinstance(data, list):
        return []
    return data


def save_tasks(tasks, path=None):
    """原子写：先写临时文件，再 replace，避免半截文件。"""
    path = path or CONFIG["data_file"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def next_id(tasks):
    """新任务 id = 当前最大 id + 1；空列表从 1 开始；删除后 id 不回收。"""
    max_id = 0
    for t in tasks:
        if isinstance(t, dict) and isinstance(t.get("id"), int):
            max_id = max(max_id, t["id"])
    return max_id + 1


def add_task(tasks, content):
    """新增任务，返回新建的任务对象。内容为空或超长抛 ValueError。"""
    content = (content or "").strip()
    if not content:
        raise ValueError("任务内容不能为空")
    if len(content) > MAX_CONTENT_LEN:
        raise ValueError("任务内容过长（上限 %d 字）" % MAX_CONTENT_LEN)
    task = {
        "id": next_id(tasks),
        "content": content,
        "done": False,
        "created_at": now_iso(),
    }
    tasks.append(task)
    return task


def toggle_task(tasks, task_id):
    """切换完成状态，返回更新后的任务；不存在抛 KeyError。"""
    for t in tasks:
        if t.get("id") == task_id:
            t["done"] = not t["done"]
            return t
    raise KeyError("任务不存在: %s" % task_id)


def delete_task(tasks, task_id):
    """删除任务；不存在抛 KeyError。"""
    for i, t in enumerate(tasks):
        if t.get("id") == task_id:
            del tasks[i]
            return
    raise KeyError("任务不存在: %s" % task_id)


def backup_tasks(path=None):
    """导入前自动备份当前数据；文件不存在返回 None。"""
    path = path or CONFIG["data_file"]
    if not os.path.exists(path):
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = os.path.join(
        os.path.dirname(path), "%s%s.json" % (BACKUP_PREFIX, ts)
    )
    shutil.copy2(path, backup_path)
    return backup_path


def validate_import(data):
    """校验导入数据：接受合法任务数组，忽略多余字段，保证 id 唯一。

    非法条目（非 dict / 空内容）被跳过；缺失或重复的 id 自动分配。
    """
    if not isinstance(data, list):
        raise ValueError("导入数据必须是任务数组")
    cleaned = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if isinstance(raw_id, int) and raw_id not in seen:
            tid = raw_id
        else:
            tid = next_id(cleaned)
        seen.add(tid)

        content = item.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue

        done = bool(item.get("done", False))
        created_at = item.get("created_at")
        if not isinstance(created_at, str):
            created_at = now_iso()

        cleaned.append(
            {"id": tid, "content": content, "done": done, "created_at": created_at}
        )
    return cleaned


# ===========================================================================
# HTTP 层
# ===========================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "DailyTodoList/0.1"
    protocol_version = "HTTP/1.0"

    # -- 响应辅助 -----------------------------------------------------------
    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def log_message(self, fmt, *args):
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    # -- 路由 ---------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", ""):
            self._serve_index()
        elif path == "/api/tasks":
            self._api_list()
        elif path == "/api/export":
            self._api_export()
        elif path.startswith("/static/"):
            self._serve_static(path)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/tasks":
            self._api_add()
        elif re.match(r"^/api/tasks/\d+/toggle$", path):
            tid = int(path.split("/")[3])
            self._api_toggle(tid)
        elif path == "/api/import":
            self._api_import()
        else:
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/tasks/(\d+)$", path)
        if m:
            self._api_delete(int(m.group(1)))
        else:
            self._send_json(404, {"error": "not found"})

    # -- 页面 / 静态 --------------------------------------------------------
    def _serve_index(self):
        index_path = os.path.join(STATIC_DIR, "index.html")
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                self._send_html(200, f.read())
        except OSError:
            self._send_html(404, "<h1>index.html 未找到</h1>")

    def _serve_static(self, path):
        rel = path[len("/static/"):]
        # 路径归一化后必须仍位于 STATIC_DIR 内，禁止 ../ 穿越
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        base = os.path.normpath(STATIC_DIR)
        if full != base and not full.startswith(base + os.sep):
            self._send_json(403, {"error": "forbidden"})
            return
        if not os.path.isfile(full):
            self._send_html(404, "<h1>not found</h1>")
            return
        ctype = "text/html" if full.endswith(".html") else "application/octet-stream"
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self._send_html(404, "<h1>not found</h1>")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- 接口 ---------------------------------------------------------------
    def _api_list(self):
        tasks = load_tasks(CONFIG["data_file"])
        self._send_json(200, {"tasks": tasks, "today": today_str()})

    def _api_add(self):
        raw = self._read_body()
        try:
            obj = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return
        content = obj.get("content") if isinstance(obj, dict) else None
        tasks = load_tasks(CONFIG["data_file"])
        try:
            task = add_task(tasks, content)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        save_tasks(tasks, CONFIG["data_file"])
        self._send_json(201, task)

    def _api_toggle(self, tid):
        tasks = load_tasks(CONFIG["data_file"])
        try:
            task = toggle_task(tasks, tid)
        except KeyError as e:
            self._send_json(404, {"error": str(e)})
            return
        save_tasks(tasks, CONFIG["data_file"])
        self._send_json(200, task)

    def _api_delete(self, tid):
        tasks = load_tasks(CONFIG["data_file"])
        try:
            delete_task(tasks, tid)
        except KeyError as e:
            self._send_json(404, {"error": str(e)})
            return
        save_tasks(tasks, CONFIG["data_file"])
        self._send_json(200, {"ok": True})

    def _api_export(self):
        tasks = load_tasks(CONFIG["data_file"])
        body = json.dumps(tasks, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="tasks.json"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_import(self):
        raw = self._read_body()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "导入数据不是合法 JSON"})
            return
        try:
            cleaned = validate_import(data)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        backup_tasks(CONFIG["data_file"])
        save_tasks(cleaned, CONFIG["data_file"])
        self._send_json(200, {"ok": True, "count": len(cleaned)})


# ===========================================================================
# 启动 / 部署
# ===========================================================================
def find_free_port(start=DEFAULT_PORT, host=HOST):
    """从 start 起找第一个可用端口。"""
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("未找到可用端口")


def setup_logging():
    handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
    try:
        handlers.append(logging.StreamHandler())
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def write_pid(pid_file=None):
    pid_file = pid_file or CONFIG["pid_file"]
    with open(pid_file, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def main():
    setup_logging()
    port = find_free_port()
    server = ThreadingHTTPServer((HOST, port), Handler)
    write_pid()
    url = "http://%s:%d/" % (HOST, port)
    with open(URL_FILE, "w", encoding="utf-8") as f:
        f.write(url)
    LOGGER.info("服务已启动：%s", url)
    # 有控制台（直接 `python app.py` 调试）时由 app.py 自行开浏览器；
    # 无控制台（双击 start.bat 走 pythonw）时交给启动脚本用 `start` 命令打开，
    # 避免 webbrowser.open 在 pythonw 下静默失败导致浏览器不弹出。
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            webbrowser.open(url)
        else:
            LOGGER.info("无控制台运行，浏览器将由启动脚本打开")
    except Exception as e:
        LOGGER.warning("自动打开浏览器失败：%s", e)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for f in (CONFIG["pid_file"], URL_FILE):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        LOGGER.info("服务已停止")


if __name__ == "__main__":
    main()
