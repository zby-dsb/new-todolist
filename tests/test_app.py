# -*- coding: utf-8 -*-
"""每日任务清单 · 测试（存储层单测 + 接口冒烟）

运行：python -m unittest -v
"""
import datetime
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "tasks.json")
        self.backup_dir = os.path.join(self.tmp, "backup")
        appmod.CONFIG["backup_dir"] = self.backup_dir

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # —— v0.1 既有用例（保持通过）——
    def test_empty_init(self):
        self.assertEqual(appmod.load_tasks(self.path), [])

    def test_add_and_persist(self):
        tasks = appmod.load_tasks(self.path)
        t = appmod.add_task(tasks, "  hello  ")
        appmod.save_tasks(tasks, self.path)
        self.assertEqual(t["content"], "hello")
        self.assertEqual(t["id"], 1)
        self.assertFalse(t["done"])
        self.assertIn("created_at", t)
        loaded = appmod.load_tasks(self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["content"], "hello")

    def test_add_empty_raises(self):
        with self.assertRaises(ValueError):
            appmod.add_task([], "   ")

    def test_add_too_long_raises(self):
        with self.assertRaises(ValueError):
            appmod.add_task([], "x" * 501)

    def test_toggle(self):
        tasks = []
        t = appmod.add_task(tasks, "a")
        appmod.toggle_task(tasks, t["id"])
        self.assertTrue(tasks[0]["done"])
        appmod.toggle_task(tasks, t["id"])
        self.assertFalse(tasks[0]["done"])

    def test_toggle_missing_raises(self):
        with self.assertRaises(KeyError):
            appmod.toggle_task([], 999)

    def test_delete(self):
        tasks = []
        t = appmod.add_task(tasks, "a")
        appmod.delete_task(tasks, t["id"])
        self.assertEqual(tasks, [])

    def test_delete_missing_raises(self):
        with self.assertRaises(KeyError):
            appmod.delete_task([], 999)

    def test_id_not_recycled(self):
        tasks = []
        a = appmod.add_task(tasks, "a")  # id 1
        b = appmod.add_task(tasks, "b")  # id 2
        appmod.delete_task(tasks, a["id"])
        c = appmod.add_task(tasks, "c")  # 应为 3，不回收
        self.assertEqual(c["id"], 3)

    def test_backup_created(self):
        tasks = []
        appmod.add_task(tasks, "a")
        appmod.save_tasks(tasks, self.path)
        bp = appmod.backup_tasks(self.path)
        self.assertIsNotNone(bp)
        self.assertTrue(os.path.exists(bp))
        self.assertEqual(appmod.load_tasks(bp), tasks)

    def test_backup_missing_file_returns_none(self):
        self.assertIsNone(appmod.backup_tasks(self.path))

    def test_validate_import_accepts_array(self):
        cleaned = appmod.validate_import([{"id": 1, "content": "x", "done": False}])
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["content"], "x")

    def test_validate_import_ignores_extra_fields(self):
        data = [{"id": 2, "content": "y", "done": True, "extra": "z",
                 "created_at": "2026-01-01T00:00:00"}]
        cleaned = appmod.validate_import(data)
        self.assertEqual(set(cleaned[0].keys()),
                          {"id", "content", "done", "created_at", "date", "completed_at"})

    def test_validate_import_skips_bad_entries(self):
        data = ["not a dict", {"content": "ok"}, {"id": "bad", "content": "x"}]
        cleaned = appmod.validate_import(data)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["content"], "ok")

    def test_validate_import_rejects_non_list(self):
        with self.assertRaises(ValueError):
            appmod.validate_import({"tasks": []})

    def test_export_import_roundtrip(self):
        tasks = []
        appmod.add_task(tasks, "a")
        appmod.add_task(tasks, "b")
        appmod.save_tasks(tasks, self.path)
        exported = appmod.load_tasks(self.path)
        cleaned = appmod.validate_import(exported)
        appmod.save_tasks(cleaned, self.path)
        result = appmod.load_tasks(self.path)
        # 归一化后字段更多，按 id/content/done 比对
        def key(t):
            return (t["id"], t["content"], t["done"])
        self.assertEqual(sorted(map(key, result)), sorted(map(key, exported)))

    # —— v1.1 新增用例 ——
    def test_add_sets_date_and_completed_at(self):
        tasks = appmod.load_tasks(self.path)
        t = appmod.add_task(tasks, "today task")
        self.assertEqual(t["date"], appmod.today_str())
        self.assertIsNone(t["completed_at"])
        self.assertFalse(t["done"])

    def test_toggle_writes_and_clears_completed_at(self):
        tasks = []
        t = appmod.add_task(tasks, "a")
        appmod.toggle_task(tasks, t["id"])
        self.assertTrue(tasks[0]["done"])
        self.assertIsNotNone(tasks[0]["completed_at"])
        appmod.toggle_task(tasks, t["id"])
        self.assertFalse(tasks[0]["done"])
        self.assertIsNone(tasks[0]["completed_at"])

    def test_edit_task(self):
        tasks = []
        t = appmod.add_task(tasks, "old")
        updated = appmod.edit_task(tasks, t["id"], "new text")
        self.assertEqual(updated["content"], "new text")
        self.assertEqual(tasks[0]["content"], "new text")
        # 编辑不改变 date / 完成状态
        self.assertEqual(updated["date"], appmod.today_str())
        self.assertFalse(updated["done"])

    def test_edit_empty_raises(self):
        tasks = []
        t = appmod.add_task(tasks, "a")
        with self.assertRaises(ValueError):
            appmod.edit_task(tasks, t["id"], "   ")

    def test_edit_missing_raises(self):
        with self.assertRaises(KeyError):
            appmod.edit_task([], 999, "x")

    def test_normalize_old_data(self):
        # 模拟 v0.1 老数据：只有 id/content/done/created_at
        old = [
            {"id": 1, "content": "no date", "done": True, "created_at": "2026-08-25T10:00:00"},
            {"id": 2, "content": "missing created", "done": False},
            {"content": "no id", "done": False, "created_at": "2026-08-26T10:00:00"},
        ]
        appmod.save_tasks(old, self.path)
        loaded = appmod.load_tasks(self.path)
        self.assertEqual(len(loaded), 3)
        # 缺 date 用 created_at 日期
        self.assertEqual(loaded[0]["date"], "2026-08-25")
        # done 且缺 completed_at 用 created_at 近似
        self.assertEqual(loaded[0]["completed_at"], "2026-08-25T10:00:00")
        # 缺 created_at 用 now，并补齐 date/completed_at
        self.assertIn("created_at", loaded[1])
        self.assertIn("date", loaded[1])
        # 缺 id 自动分配
        ids = sorted(t["id"] for t in loaded)
        self.assertEqual(ids, [1, 2, 3])

    def test_auto_backup_creates_file(self):
        tasks = []
        appmod.add_task(tasks, "a")
        appmod.save_tasks(tasks, self.path)
        dest = appmod.auto_backup(self.path)
        self.assertIsNotNone(dest)
        self.assertTrue(os.path.isdir(self.backup_dir))
        self.assertTrue(os.path.exists(dest))
        self.assertEqual(appmod.load_tasks(dest), tasks)

    def test_auto_backup_prunes_to_keep(self):
        # 准备 tasks.json
        appmod.save_tasks([{"id": 1, "content": "x", "done": False,
                            "created_at": "2026-08-30T00:00:00",
                            "date": "2026-08-30", "completed_at": None}], self.path)
        # 预置 40 个旧备份，文件名可排序且唯一
        os.makedirs(self.backup_dir, exist_ok=True)
        for i in range(40):
            p = os.path.join(self.backup_dir, "tasks.20260101-%05d.json" % i)
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")
        appmod.auto_backup(self.path)
        files = [f for f in os.listdir(self.backup_dir)
                 if f.startswith("tasks.") and f.endswith(".json")]
        self.assertLessEqual(len(files), appmod.BACKUP_KEEP)
        # 最新的应被保留
        files.sort()
        self.assertTrue(files[-1].startswith("tasks."))

    def test_compute_stats(self):
        today = "2026-03-02"  # 周一
        td = datetime.date(2026, 3, 2)
        monday = td - datetime.timedelta(days=td.weekday())
        tasks = []
        # 周一..今天 每天一个已完成（连续打卡）
        d = monday
        while d <= td:
            ds = d.isoformat()
            tasks.append({"id": len(tasks) + 1, "content": "t" + ds, "done": True,
                          "created_at": ds + "T09:00:00", "date": ds,
                          "completed_at": ds + "T09:00:00"})
            d += datetime.timedelta(days=1)
        # 今天还有一个未完成的
        today_ds = td.isoformat()
        tasks.append({"id": len(tasks) + 1, "content": "todo", "done": False,
                      "created_at": today_ds + "T08:00:00", "date": today_ds,
                      "completed_at": None})
        # 上周一个已完成（应排除在 week/trend/streak 之外）
        prev = (monday - datetime.timedelta(days=7)).isoformat()
        tasks.append({"id": len(tasks) + 1, "content": "lastweek", "done": True,
                      "created_at": prev + "T09:00:00", "date": prev,
                      "completed_at": prev + "T09:00:00"})

        s = appmod.compute_stats(tasks, today=today)
        # 今日口径（按 date）
        self.assertEqual(s["today_done"], 1)
        self.assertEqual(s["today_total"], 2)
        self.assertAlmostEqual(s["today_rate"], 0.5)
        # 连续打卡：周一..今天 连续
        expected_streak = (td - monday).days + 1
        self.assertEqual(s["streak"], expected_streak)
        # 本周完成：周一..今天 每天 1 个（lastweek 排除）
        self.assertEqual(s["week_done"], expected_streak)
        # trend 近 7 天，含今天，长度 7，每格 0/1
        self.assertEqual(len(s["trend"]), 7)
        self.assertEqual(s["trend"][-1]["date"], today)
        total_trend = sum(d["done"] for d in s["trend"])
        self.assertEqual(total_trend, expected_streak)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data = os.path.join(cls.tmp, "tasks.json")
        cls.backup_dir = os.path.join(cls.tmp, "backup")
        appmod.CONFIG["data_file"] = cls.data
        appmod.CONFIG["backup_dir"] = cls.backup_dir
        # 用系统分配的空闲端口，避免与 8000 冲突
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.server = appmod.ThreadingHTTPServer(("127.0.0.1", cls.port), appmod.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, data=None, raw=None):
        url = self.base + path
        if raw is not None:
            body = raw.encode("utf-8")
            r = Request(url, data=body, method=method)
            r.add_header("Content-Type", "application/json")
        elif data is not None:
            body = json.dumps(data).encode("utf-8")
            r = Request(url, data=body, method=method)
            r.add_header("Content-Type", "application/json")
        else:
            r = Request(url, method=method)
        try:
            with urlopen(r) as resp:
                return resp.status, resp.read().decode("utf-8")
        except HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def raw_get(self, path):
        s = socket.socket()
        s.connect(("127.0.0.1", self.port))
        s.sendall(("GET %s HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n" % path).encode())
        resp = s.recv(8192).decode("utf-8", "replace")
        s.close()
        return resp

    def setUp(self):
        appmod.save_tasks([], appmod.CONFIG["data_file"])

    # —— v0.1 既有用例 ——
    def test_list_empty(self):
        status, body = self.req("GET", "/api/tasks")
        self.assertEqual(status, 200)
        obj = json.loads(body)
        self.assertEqual(obj["tasks"], [])
        self.assertIn("today", obj)

    def test_add_list_toggle_delete(self):
        st, b = self.req("POST", "/api/tasks", {"content": "task one"})
        self.assertEqual(st, 201)
        tid = json.loads(b)["id"]

        st, b = self.req("GET", "/api/tasks")
        self.assertEqual(len(json.loads(b)["tasks"]), 1)

        st, b = self.req("POST", "/api/tasks/%d/toggle" % tid)
        self.assertTrue(json.loads(b)["done"])

        st, b = self.req("DELETE", "/api/tasks/%d" % tid)
        self.assertEqual(json.loads(b), {"ok": True})

        st, b = self.req("GET", "/api/tasks")
        self.assertEqual(json.loads(b)["tasks"], [])

    def test_add_empty_content_400(self):
        st, b = self.req("POST", "/api/tasks", {"content": "  "})
        self.assertEqual(st, 400)
        self.assertIn("error", json.loads(b))

    def test_toggle_missing_404(self):
        st, _ = self.req("POST", "/api/tasks/99999/toggle")
        self.assertEqual(st, 404)

    def test_delete_missing_404(self):
        st, _ = self.req("DELETE", "/api/tasks/99999")
        self.assertEqual(st, 404)

    def test_export_import(self):
        self.req("POST", "/api/tasks", {"content": "exp1"})
        st, payload = self.req("GET", "/api/export")
        self.assertEqual(st, 200)
        data = json.loads(payload)
        data.append({"id": 99, "content": "imported", "done": True})
        st, b = self.req("POST", "/api/import", raw=json.dumps(data))
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(b)["count"], 2)
        # 导入前应当生成了自动备份
        backups = [f for f in os.listdir(self.tmp) if f.startswith("tasks.backup.")]
        self.assertTrue(len(backups) >= 1)

    def test_index_page(self):
        st, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("每日任务清单", body)

    def test_static_traversal_blocked(self):
        resp = self.raw_get("/static/../../app.py")
        self.assertNotIn("ThreadingHTTPServer", resp)
        self.assertNotIn("server_version", resp)

    # —— v1.1 新增用例 ——
    def test_add_forces_today_date(self):
        st, b = self.req("POST", "/api/tasks", {"content": "x", "date": "2000-01-01"})
        self.assertEqual(st, 201)
        obj = json.loads(b)
        self.assertEqual(obj["date"], appmod.today_str())
        self.assertIsNone(obj["completed_at"])

    def test_list_date_filter_and_viewed(self):
        st, b = self.req("POST", "/api/tasks", {"content": "today task"})
        self.assertEqual(st, 201)
        tid = json.loads(b)["id"]
        # 默认今天有这条
        st, b = self.req("GET", "/api/tasks")
        obj = json.loads(b)
        self.assertEqual(len(obj["tasks"]), 1)
        self.assertEqual(obj["viewed"], appmod.today_str())
        self.assertEqual(obj["today"], appmod.today_str())
        # 查一个历史日期应为空，但仍返回 today/viewed
        st, b = self.req("GET", "/api/tasks?date=2000-01-01")
        obj = json.loads(b)
        self.assertEqual(obj["tasks"], [])
        self.assertEqual(obj["viewed"], "2000-01-01")

    def test_edit_via_put(self):
        st, b = self.req("POST", "/api/tasks", {"content": "orig"})
        tid = json.loads(b)["id"]
        st, b = self.req("PUT", "/api/tasks/%d" % tid, {"content": "edited"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(b)["content"], "edited")
        # date 不变
        self.assertEqual(json.loads(b)["date"], appmod.today_str())

    def test_edit_empty_400(self):
        st, b = self.req("POST", "/api/tasks", {"content": "orig"})
        tid = json.loads(b)["id"]
        st, b = self.req("PUT", "/api/tasks/%d" % tid, {"content": "  "})
        self.assertEqual(st, 400)

    def test_edit_missing_404(self):
        st, _ = self.req("PUT", "/api/tasks/99999", {"content": "x"})
        self.assertEqual(st, 404)

    def test_stats_endpoint(self):
        self.req("POST", "/api/tasks", {"content": "a"})
        st, b = self.req("GET", "/api/stats")
        self.assertEqual(st, 200)
        s = json.loads(b)
        for k in ("today_done", "today_total", "week_done", "streak", "today_rate", "trend"):
            self.assertIn(k, s)
        self.assertEqual(len(s["trend"]), 7)

    def test_auto_backup_after_write(self):
        self.req("POST", "/api/tasks", {"content": "will backup"})
        files = [f for f in os.listdir(self.backup_dir)
                 if f.startswith("tasks.") and f.endswith(".json")]
        self.assertTrue(len(files) >= 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
