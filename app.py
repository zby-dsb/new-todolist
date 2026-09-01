# -*- coding: utf-8 -*-
"""
每日任务清单 · 后端 (v1.1)

技术约束（见 docs/技术方案_v1.1.md）：
- 仅使用 Python 3 标准库，零第三方依赖
- 监听 127.0.0.1，本机单机使用，不做登录鉴权
- 数据存本地 tasks.json，读写均显式 UTF-8，原子写
- 业务逻辑（存储层）与 HTTP 处理分离，便于单测
- v1.1：每日视图（date）/ 统计打卡回顾 / 就地编辑 / 每次写后自动备份
"""
import datetime
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import logging
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# 配置（模块级，便于测试时猴子补丁）
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_FILE = os.path.join(BASE_DIR, "tasks.json")
PID_FILE = os.path.join(BASE_DIR, "server.pid")
LOG_FILE = os.path.join(BASE_DIR, "app.log")
URL_FILE = os.path.join(BASE_DIR, "app.url")
BACKUP_DIR = os.path.join(BASE_DIR, "backup")          # v1.1 每次写后自动备份目录
BACKUP_PREFIX = "tasks.backup."                        # v0.1 导入前备份前缀
MAX_CONTENT_LEN = 500
DEFAULT_PORT = 8000
HOST = "127.0.0.1"
BACKUP_KEEP = 30                                       # 自动备份最多保留份数

CONFIG = {
    "data_file": DATA_FILE,
    "pid_file": PID_FILE,
    "log_file": LOG_FILE,
    "backup_dir": BACKUP_DIR,
}

LOGGER = logging.getLogger("todolist")


# ===========================================================================
# 存储层（纯函数，不依赖 socket，便于单测）
# ===========================================================================
def now_iso():
    """当前时间，ISO 格式（YYYY-MM-DDTHH:MM:SS）。"""
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str():
    """今天日期，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def _parse_date(s):
    """把 'YYYY-MM-DD' 解析为 date，失败返回 None。"""
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, IndexError):
        return None


def _parse_dt(s):
    """把 'YYYY-MM-DDTHH:MM:SS' 解析为 datetime，失败返回 None。"""
    if not isinstance(s, str):
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def next_id(tasks):
    """新任务 id = 当前最大 id + 1；空列表从 1 开始；删除后 id 不回收。"""
    max_id = 0
    for t in tasks:
        if isinstance(t, dict) and isinstance(t.get("id"), int):
            max_id = max(max_id, t["id"])
    return max_id + 1


def normalize_tasks(tasks):
    """补齐 / 修正字段，保证旧数据升级到 v1.1 不丢、不报错。

    - 非法条目（非 dict / 空内容）跳过
    - 缺 date 用 created_at 日期；done 且缺 completed_at 用 created_at 近似
    - 缺 created_at 用现在时间
    - 缺失或重复 id 自动分配
    """
    out = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        content = t.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue
        done = bool(t.get("done", False))
        created_at = t.get("created_at")
        if not isinstance(created_at, str):
            created_at = now_iso()
        date = t.get("date")
        if not isinstance(date, str) or not date:
            date = created_at[:10] if len(created_at) >= 10 else today_str()
        if done:
            completed_at = t.get("completed_at")
            if not isinstance(completed_at, str):
                completed_at = created_at  # 近似：以创建时间作为完成时间
        else:
            completed_at = None
        out.append({
            "id": t.get("id"),
            "content": content,
            "done": done,
            "created_at": created_at,
            "date": date,
            "completed_at": completed_at,
        })
    # 分配缺失 / 重复 id
    seen = set()
    max_id = 0
    for t in out:
        if isinstance(t["id"], int) and t["id"] not in seen:
            seen.add(t["id"])
            max_id = max(max_id, t["id"])
        else:
            t["id"] = None
    for t in out:
        if t["id"] is None:
            max_id += 1
            t["id"] = max_id
    return out


def load_tasks(path=None):
    """读取任务列表；文件不存在或损坏时返回空列表，并自动归一化旧数据。"""
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
    return normalize_tasks(data)


def save_tasks(tasks, path=None):
    """原子写：先写临时文件，再 replace，避免半截文件。"""
    path = path or CONFIG["data_file"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def add_task(tasks, content):
    """新增任务（date=今天，completed_at=None）。内容为空或超长抛 ValueError。"""
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
        "date": today_str(),          # v1.1：归属今天
        "completed_at": None,
    }
    tasks.append(task)
    return task


def toggle_task(tasks, task_id):
    """切换完成状态；完成时写 completed_at，取消则清空。不存在抛 KeyError。"""
    for t in tasks:
        if t.get("id") == task_id:
            t["done"] = not t["done"]
            if t["done"]:
                t["completed_at"] = now_iso()
            else:
                t["completed_at"] = None
            return t
    raise KeyError("任务不存在: %s" % task_id)


def edit_task(tasks, task_id, content):
    """编辑任务内容（仅 content，date/完成状态不变）。"""
    content = (content or "").strip()
    if not content:
        raise ValueError("任务内容不能为空")
    if len(content) > MAX_CONTENT_LEN:
        raise ValueError("任务内容过长（上限 %d 字）" % MAX_CONTENT_LEN)
    for t in tasks:
        if t.get("id") == task_id:
            t["content"] = content
            return t
    raise KeyError("任务不存在: %s" % task_id)


def delete_task(tasks, task_id):
    """删除任务；不存在抛 KeyError。"""
    for i, t in enumerate(tasks):
        if t.get("id") == task_id:
            del tasks[i]
            return
    raise KeyError("任务不存在: %s" % task_id)


def auto_backup(path=None):
    """v1.1：每次成功写操作后，把当前 tasks.json 复制到 backup/ 目录（按时间戳命名），
    并保留最近 BACKUP_KEEP 份，超出删最旧。文件不存在返回 None。"""
    path = path or CONFIG["data_file"]
    if not os.path.exists(path):
        return None
    backup_dir = CONFIG["backup_dir"]
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except OSError:
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, "tasks.%s.json" % ts)
    try:
        shutil.copy2(path, dest)
    except OSError as e:
        LOGGER.warning("自动备份失败：%s", e)
        return None
    # 裁剪到最近 BACKUP_KEEP 份
    try:
        files = [f for f in os.listdir(backup_dir)
                 if f.startswith("tasks.") and f.endswith(".json")]
        files.sort()
        while len(files) > BACKUP_KEEP:
            old = files.pop(0)
            try:
                os.remove(os.path.join(backup_dir, old))
            except OSError:
                pass
    except OSError:
        pass
    return dest


def backup_tasks(path=None):
    """v0.1 导入前自动备份当前数据（tasks.backup.<时间戳>.json），与 v1.1 自动备份并存。"""
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
    """校验导入数据：接受合法任务数组，忽略多余字段，保证 id 唯一、字段完整。

    非法条目（非 dict / 空内容）被跳过；缺失或重复的 id 自动分配；
    最终经 normalize_tasks 补齐 date / completed_at 等字段。
    """
    if not isinstance(data, list):
        raise ValueError("导入数据必须是任务数组")
    raw = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = item.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            continue
        raw.append({
            "id": item.get("id"),
            "content": content,
            "done": bool(item.get("done", False)),
            "created_at": item.get("created_at") if isinstance(item.get("created_at"), str) else None,
            "date": item.get("date") if isinstance(item.get("date"), str) else None,
            "completed_at": item.get("completed_at") if isinstance(item.get("completed_at"), str) else None,
        })
    return normalize_tasks(raw)


def compute_stats(tasks, today=None):
    """统计与打卡回顾。

    - today_done/today_total：按 date==today 且 done 统计（该日口径）
    - week_done：本周一 00:00 到当前，done 且 completed_at 落在该区间的数量
    - streak：从今天往前，连续每天有「done 且 date==当天」的最长天数（今天无完成则 0）
    - today_rate：today_done / today_total（无任务记 0）
    - trend：最近 7 天（含今天），按 completed_at 落在该天的完成数
    """
    if today is None:
        today = today_str()
    today_d = _parse_date(today)

    # 今日（按 date 口径）
    today_tasks = [t for t in tasks if t.get("date") == today]
    today_total = len(today_tasks)
    today_done = sum(1 for t in today_tasks if t.get("done"))
    today_rate = (today_done / today_total) if today_total else 0.0

    week_done = 0
    streak = 0
    trend = []
    if today_d is not None:
        # 本周：周一为起点
        monday = today_d - datetime.timedelta(days=today_d.weekday())
        monday_start = datetime.datetime(monday.year, monday.month, monday.day, 0, 0, 0)
        now_dt = datetime.datetime.now()
        for t in tasks:
            if not t.get("done"):
                continue
            ca = _parse_dt(t.get("completed_at"))
            if ca and monday_start <= ca <= now_dt:
                week_done += 1

        # 连续打卡：从今天往前数
        day = today_d
        while True:
            day_str = day.isoformat()
            if any(t.get("date") == day_str and t.get("done") for t in tasks):
                streak += 1
                day = day - datetime.timedelta(days=1)
            else:
                break

        # 近 7 天趋势（按 completed_at 落点）
        for i in range(6, -1, -1):
            d = today_d - datetime.timedelta(days=i)
            d_str = d.isoformat()
            cnt = 0
            for t in tasks:
                if not t.get("done"):
                    continue
                ca = _parse_dt(t.get("completed_at"))
                if ca and ca.date().isoformat() == d_str:
                    cnt += 1
            trend.append({"date": d_str, "done": cnt})

    return {
        "today_done": today_done,
        "today_total": today_total,
        "week_done": week_done,
        "streak": streak,
        "today_rate": today_rate,
        "trend": trend,
    }


# ===========================================================================
# HTTP 层
# ===========================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "DailyTodoList/1.1"
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
        elif path == "/api/stats":
            self._api_stats()
        elif path == "/api/export":
            self._api_export()
        elif path.startswith("/static/"):
            self._serve_static(path)
        elif path == "/store.js":
            # index.html 由 "/" 提供，页面内用相对路径 "./store.js" 引入存储模块。
            # 相对路径是刻意的：手机端（Capacitor）webDir 就是 static/，
            # "./store.js" 会解析成 https://<host>/store.js —— 正好对得上。
            # 若改成 "/static/store.js"，手机端反而会 404。
            # 所以这里让根路径也能取到 store.js，两端用同一份代码。
            self._serve_static("/static/store.js")
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

    def do_PUT(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/tasks/(\d+)$", path)
        if m:
            self._api_edit(int(m.group(1)))
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

    @staticmethod
    def _guess_ctype(path):
        """推断静态文件的 Content-Type。

        关键点：ES module 对 MIME 有严格要求 —— .js 必须是 text/javascript。
        原先这里把非 .html 一律返回 application/octet-stream，浏览器会直接
        拒绝执行模块（"Failed to load module script"），导致整个页面白屏。
        """
        ext = os.path.splitext(path)[1].lower()
        if ext in (".js", ".mjs"):
            return "text/javascript"
        if ext == ".html":
            return "text/html"
        if ext == ".css":
            return "text/css"
        if ext == ".json":
            return "application/json"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

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
        ctype = self._guess_ctype(full)
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
        params = parse_qs(urlparse(self.path).query)
        date_vals = params.get("date")
        viewed = date_vals[0] if date_vals else today_str()
        if not _parse_date(viewed):
            viewed = today_str()
        tasks = load_tasks(CONFIG["data_file"])
        filtered = [t for t in tasks if t.get("date") == viewed]
        self._send_json(200, {"tasks": filtered, "today": today_str(), "viewed": viewed})

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
            task = add_task(tasks, content)  # date 强制为今天，忽略传入 date
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        save_tasks(tasks, CONFIG["data_file"])
        auto_backup(CONFIG["data_file"])
        self._send_json(201, task)

    def _api_toggle(self, tid):
        tasks = load_tasks(CONFIG["data_file"])
        try:
            task = toggle_task(tasks, tid)
        except KeyError as e:
            self._send_json(404, {"error": str(e)})
            return
        save_tasks(tasks, CONFIG["data_file"])
        auto_backup(CONFIG["data_file"])
        self._send_json(200, task)

    def _api_edit(self, tid):
        raw = self._read_body()
        try:
            obj = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return
        content = obj.get("content") if isinstance(obj, dict) else None
        tasks = load_tasks(CONFIG["data_file"])
        try:
            task = edit_task(tasks, tid, content)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        except KeyError as e:
            self._send_json(404, {"error": str(e)})
            return
        save_tasks(tasks, CONFIG["data_file"])
        auto_backup(CONFIG["data_file"])
        self._send_json(200, task)

    def _api_delete(self, tid):
        tasks = load_tasks(CONFIG["data_file"])
        try:
            delete_task(tasks, tid)
        except KeyError as e:
            self._send_json(404, {"error": str(e)})
            return
        save_tasks(tasks, CONFIG["data_file"])
        auto_backup(CONFIG["data_file"])
        self._send_json(200, {"ok": True})

    def _api_stats(self):
        tasks = load_tasks(CONFIG["data_file"])
        self._send_json(200, compute_stats(tasks))

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
        backup_tasks(CONFIG["data_file"])          # v0.1 导入前备份（并存）
        save_tasks(cleaned, CONFIG["data_file"])
        auto_backup(CONFIG["data_file"])           # v1.1 写后自动备份
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
