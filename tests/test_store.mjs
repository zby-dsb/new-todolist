// tests/test_store.mjs
// store.js 的 node --test 单测（零第三方依赖，直接 import ES module）
import { test } from "node:test";
import assert from "node:assert/strict";
import * as S from "../static/store.js";

// 每个用例前清空存储，避免互相污染
function reset() {
  S.clearAll();
}

// 造一个带日期/完成时间的任务
function mk(id, content, date, done, doneOffset = 0) {
  const created = date + "T09:00:00";
  return {
    id,
    content,
    date,
    done,
    created_at: created,
    completed_at: done ? date + "T" + String(9 + doneOffset).padStart(2, "0") + ":30:00" : null,
  };
}

test("nextId 在空列表从 1 开始，删除后不回收", () => {
  reset();
  assert.equal(S.nextId([]), 1);
  const tasks = [mk(1, "a", "2026-09-01", false), mk(3, "b", "2026-09-01", false)];
  assert.equal(S.nextId(tasks), 4);
});

test("addTask 新增归属今天且未完成，内容空/超长抛错", () => {
  reset();
  const tasks = [];
  const t = S.addTask(tasks, "  写代码  ");
  assert.equal(t.content, "写代码"); // 自动 trim
  assert.equal(t.done, false);
  assert.equal(t.completed_at, null);
  assert.ok(/^\d{4}-\d{2}-\d{2}$/.test(t.date)); // date = 今天
  assert.equal(tasks.length, 1);

  assert.throws(() => S.addTask(tasks, "   "), /不能为空/);
  assert.throws(() => S.addTask(tasks, "x".repeat(501)), /过长/);
});

test("toggleTask 完成时写 completed_at，取消则清空", () => {
  reset();
  const tasks = [mk(1, "a", "2026-09-01", false)];
  const t = S.toggleTask(tasks, 1);
  assert.equal(t.done, true);
  assert.ok(t.completed_at && t.completed_at.startsWith("20"));
  S.toggleTask(tasks, 1);
  assert.equal(tasks[0].done, false);
  assert.equal(tasks[0].completed_at, null);

  assert.throws(() => S.toggleTask(tasks, 999), /不存在/);
});

test("editTask 只改内容，date/完成状态不变", () => {
  reset();
  const tasks = [mk(1, "旧", "2026-09-01", true, 1)];
  const t = S.editTask(tasks, 1, " 新内容 ");
  assert.equal(t.content, "新内容");
  assert.equal(t.date, "2026-09-01"); // date 不变
  assert.equal(t.done, true); // 完成状态不变
  assert.throws(() => S.editTask(tasks, 1, ""), /不能为空/);
  assert.throws(() => S.editTask(tasks, 999, "x"), /不存在/);
});

test("deleteTask 删除指定任务，不存在抛错", () => {
  reset();
  const tasks = [mk(1, "a", "2026-09-01", false), mk(2, "b", "2026-09-01", false)];
  S.deleteTask(tasks, 1);
  assert.equal(tasks.length, 1);
  assert.equal(tasks[0].id, 2);
  assert.throws(() => S.deleteTask(tasks, 1), /不存在/);
});

test("importData 做字段归一化，缺 date 用 created_at 日期补齐", () => {
  reset();
  const data = [
    { id: 1, content: " 任务A ", done: true, created_at: "2026-09-01T08:00:00", completed_at: "2026-09-01T09:00:00" },
    { content: "任务B", done: false }, // 缺 id/date/created_at
    { content: "" }, // 空内容应被跳过
    "不是对象", // 非对象跳过
  ];
  const out = S.importData(data);
  assert.equal(out.length, 2);
  const a = out.find((t) => t.id === 1);
  assert.equal(a.content, "任务A");
  assert.equal(a.date, "2026-09-01");
  const b = out.find((t) => t.content === "任务B");
  assert.ok(b.id > 0);
  assert.ok(/^\d{4}-\d{2}-\d{2}$/.test(b.date));
  assert.equal(b.done, false);
  assert.equal(b.completed_at, null);

  assert.throws(() => S.importData({ not: "array" }), /必须是任务数组/);
});

test("snapshot / undo 撤销上一次改动", () => {
  reset();
  const tasks = [mk(1, "a", "2026-09-01", false), mk(2, "b", "2026-09-01", false)];
  S.snapshot(tasks); // 改动前存一份
  S.deleteTask(tasks, 1); // 删掉一个
  assert.equal(tasks.length, 1);
  const restored = S.undo();
  assert.ok(restored);
  assert.equal(restored.length, 2); // 回到删除前
  assert.deepEqual(restored.map((t) => t.id).sort(), [1, 2]);

  // 再 undo 一次应返回 null（已无快照）
  assert.equal(S.undo(), null);
});

test("computeStats 今日/本周/连续打卡/完成率/近7天趋势", () => {
  reset();
  const t = "2026-09-01"; // 周二
  const tasks = [
    mk(1, "今天完成1", t, true, 1),
    mk(2, "今天未完成", t, false),
    mk(3, "昨天完成", "2026-08-31", true, 1),
    mk(4, "前天完成", "2026-08-30", true, 1),
    mk(5, "大前天完成", "2026-08-29", true, 1),
  ];
  const s = S.computeStats(tasks, t);
  assert.equal(s.today_total, 2);
  assert.equal(s.today_done, 1);
  assert.equal(s.today_rate, 0.5);

  // 本周一 = 2026-08-31(周一)。08-31..09-01 内完成的都算
  assert.ok(s.week_done >= 2);

  // 连续打卡：09-01(完成), 08-31(完成), 08-30(完成), 08-29(完成) → 连续 4 天
  assert.equal(s.streak, 4);

  // 趋势长度 7，且最后一项 date == 今天，done>=1
  assert.equal(s.trend.length, 7);
  assert.equal(s.trend[6].date, t);
  assert.ok(s.trend[6].done >= 1);
  // 趋势中完成的累计应包含所有 done 任务的日期
  const totalTrendDone = s.trend.reduce((a, d) => a + d.done, 0);
  assert.equal(totalTrendDone, 4); // 4 个已完成任务都落在近 7 天
});

test("computeStats 空数据不报错", () => {
  reset();
  const s = S.computeStats([], "2026-09-01");
  assert.equal(s.today_total, 0);
  assert.equal(s.today_rate, 0);
  assert.equal(s.streak, 0);
  assert.equal(s.trend.length, 7);
});

test("load/save 经存储抽象落盘（Node 走内存回退）", () => {
  reset();
  const tasks = [mk(1, "a", "2026-09-01", false)];
  S.save(tasks);
  const loaded = S.load();
  assert.equal(loaded.length, 1);
  assert.equal(loaded[0].content, "a");
});

test("normalizeTasks 补齐缺失字段、跳过脏数据", () => {
  reset();
  const raw = [
    { id: 1, content: "正常", date: "2026-09-01", done: true, created_at: "2026-09-01T08:00:00", completed_at: "2026-09-01T09:00:00" },
    { content: "无id", done: false }, // 无 id，应被分配
    { content: "" }, // 空内容跳过
    null,
  ];
  const out = S.normalizeTasks(raw);
  assert.equal(out.length, 2);
  // 第一个保留原 id
  assert.equal(out[0].id, 1);
  // 第二个被分配到新 id
  assert.ok(out[1].id > 0);
  assert.ok(out[1].date);
});
