# -*- coding: utf-8 -*-
"""每日任务清单 · 测试（存储层单测 + 接口冒烟）

运行：python -m unittest -v
"""
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

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

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
        self.assertEqual(set(cleaned[0].keys()), {"id", "content", "done", "created_at"})

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
        self.assertEqual(appmod.load_tasks(self.path), exported)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data = os.path.join(cls.tmp, "tasks.json")
        cls.backup_dir = cls.tmp  # 备份与 data 同目录
        appmod.CONFIG["data_file"] = cls.data
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
            req = Request(url, data=body, method=method)
            req.add_header("Content-Type", "application/json")
        elif data is not None:
            body = json.dumps(data).encode("utf-8")
            req = Request(url, data=body, method=method)
            req.add_header("Content-Type", "application/json")
        else:
            req = Request(url, method=method)
        try:
            with urlopen(req) as r:
                return r.status, r.read().decode("utf-8")
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
        # 原始路径穿越应被拦截，不能泄露源码
        resp = self.raw_get("/static/../../app.py")
        self.assertNotIn("ThreadingHTTPServer", resp)
        self.assertNotIn("server_version", resp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
