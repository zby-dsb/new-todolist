// static/store.js
// 手机端存储 + 统计 + 备份逻辑（ES module，可被 node --test 直接 import）
//
// 设计要点：
// - 纯函数（normalizeTasks / addTask / toggleTask / editTask / deleteTask / computeStats / importData）
//   只操作「任务数组」，不依赖浏览器/网络，便于单测。
// - 存储层 load/save/loadSnaps/saveSnaps/snapshot/undo 通过 _storage() 抽象：
//   浏览器用 localStorage；Node（测试）下自动回退到内存 Map，保证测试可跑。
// - 导出 exportData 在真机走 Filesystem 落盘 + Share 调系统分享（见技术方案 9 节）；
//   非真机（桌面预览）回退到 Blob 下载。Capacitor 插件用动态 import，避免污染 Node 导入。
//
// 存储键名（与 DESIGN_手机端_v1.0.md 第 5 节一致）：
//   TASKS_KEY  = "dailytodo.tasks.v1"
//   SNAPS_KEY  = "dailytodo.snapshots.v1"（保留最近 5 份，用于误删还原）

export const TASKS_KEY = "dailytodo.tasks.v1";
export const SNAPS_KEY = "dailytodo.snapshots.v1";
export const SNAP_KEEP = 5;
export const MAX_CONTENT_LEN = 500;

// ---------------------------------------------------------------------------
// 时间辅助
// ---------------------------------------------------------------------------
export function nowIso() {
  // 本地时间，格式 YYYY-MM-DDTHH:MM:SS（与 Python strftime 一致，不用 UTC）
  const d = new Date();
  const p = (n) => (n < 10 ? "0" + n : "" + n);
  return (
    d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
    "T" + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds())
  );
}

export function todayStr() {
  const d = new Date();
  const p = (n) => (n < 10 ? "0" + n : "" + n);
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

// 把 Date 转回 YYYY-MM-DD 字符串
export function isoDate(d) {
  const p = (n) => (n < 10 ? "0" + n : "" + n);
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

// 'YYYY-MM-DD' -> 本地 Date(零点) 或 null
export function parseDate(s) {
  if (typeof s !== "string" || s.length < 10) return null;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const y = +m[1], mo = +m[2], d = +m[3];
  const dt = new Date(y, mo - 1, d);
  if (isNaN(dt) || dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) return null;
  return dt;
}

// 'YYYY-MM-DDTHH:MM:SS' -> 本地 Date 或 null
export function parseDt(s) {
  if (typeof s !== "string") return null;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  const dt = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  if (isNaN(dt)) return null;
  return dt;
}

// ---------------------------------------------------------------------------
// 存储抽象：浏览器用 localStorage，Node 测试回退到内存 Map
// ---------------------------------------------------------------------------
let _mem = null;
function _memStorage() {
  if (!_mem) {
    const m = new Map();
    _mem = {
      getItem: (k) => (m.has(k) ? m.get(k) : null),
      setItem: (k, v) => m.set(k, String(v)),
      removeItem: (k) => m.delete(k),
      clear: () => m.clear(),
    };
  }
  return _mem;
}
function _storage() {
  try {
    if (typeof localStorage !== "undefined" && localStorage) return localStorage;
  } catch (_) { /* 某些环境访问 localStorage 会抛错 */ }
  return _memStorage();
}

// 测试辅助：清空两把存储键
export function clearAll() {
  try { _storage().removeItem(TASKS_KEY); } catch (_) {}
  try { _storage().removeItem(SNAPS_KEY); } catch (_) {}
}

// ---------------------------------------------------------------------------
// 纯函数：数据归一化
// ---------------------------------------------------------------------------
export function nextId(tasks) {
  let max = 0;
  for (const t of tasks) {
    if (t && typeof t.id === "number" && t.id > max) max = t.id;
  }
  return max + 1;
}

// 补齐 / 修正字段，保证旧数据升级不丢不报错（移植自 app.py: normalize_tasks）
export function normalizeTasks(tasks) {
  if (!Array.isArray(tasks)) return [];
  const out = [];
  for (const t of tasks) {
    if (typeof t !== "object" || t === null) continue;
    let content = t.content;
    if (typeof content !== "string") content = String(content);
    content = content.trim();
    if (!content) continue; // 空内容跳过
    const done = !!t.done;
    let created_at = t.created_at;
    if (typeof created_at !== "string") created_at = nowIso();
    let date = t.date;
    if (typeof date !== "string" || !date) {
      date = created_at.length >= 10 ? created_at.slice(0, 10) : todayStr();
    }
    let completed_at;
    if (done) {
      completed_at = t.completed_at;
      if (typeof completed_at !== "string") completed_at = created_at; // 近似
    } else {
      completed_at = null;
    }
    out.push({ id: t.id, content, done, created_at, date, completed_at });
  }
  // 分配缺失 / 重复 id
  const seen = new Set();
  let maxId = 0;
  for (const t of out) {
    if (typeof t.id === "number" && t.id > 0 && !seen.has(t.id)) {
      seen.add(t.id);
      maxId = Math.max(maxId, t.id);
    } else {
      t.id = null;
    }
  }
  for (const t of out) {
    if (t.id === null) {
      maxId += 1;
      t.id = maxId;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// 纯函数：增删改 / 完成状态
// ---------------------------------------------------------------------------
export function addTask(tasks, content) {
  content = (content || "").trim();
  if (!content) throw new Error("任务内容不能为空");
  if (content.length > MAX_CONTENT_LEN) throw new Error("任务内容过长（上限 " + MAX_CONTENT_LEN + " 字）");
  const task = {
    id: nextId(tasks),
    content,
    done: false,
    created_at: nowIso(),
    date: todayStr(),
    completed_at: null,
  };
  tasks.push(task);
  return task;
}

export function toggleTask(tasks, taskId) {
  for (const t of tasks) {
    if (t.id === taskId) {
      t.done = !t.done;
      t.completed_at = t.done ? nowIso() : null;
      return t;
    }
  }
  throw new Error("任务不存在: " + taskId);
}

export function editTask(tasks, taskId, content) {
  content = (content || "").trim();
  if (!content) throw new Error("任务内容不能为空");
  if (content.length > MAX_CONTENT_LEN) throw new Error("任务内容过长（上限 " + MAX_CONTENT_LEN + " 字）");
  for (const t of tasks) {
    if (t.id === taskId) {
      t.content = content;
      return t;
    }
  }
  throw new Error("任务不存在: " + taskId);
}

export function deleteTask(tasks, taskId) {
  for (let i = 0; i < tasks.length; i++) {
    if (tasks[i].id === taskId) {
      tasks.splice(i, 1);
      return;
    }
  }
  throw new Error("任务不存在: " + taskId);
}

// ---------------------------------------------------------------------------
// 纯函数：导入（字段归一化；导入语义 = 覆盖）
// ---------------------------------------------------------------------------
export function importData(data) {
  if (!Array.isArray(data)) throw new Error("导入数据必须是任务数组");
  const raw = [];
  for (const item of data) {
    if (typeof item !== "object" || item === null) continue;
    let content = item.content;
    if (typeof content !== "string") content = String(content);
    if (!content.trim()) continue;
    raw.push({
      id: item.id,
      content,
      done: !!item.done,
      created_at: typeof item.created_at === "string" ? item.created_at : null,
      date: typeof item.date === "string" ? item.date : null,
      completed_at: typeof item.completed_at === "string" ? item.completed_at : null,
    });
  }
  return normalizeTasks(raw);
}

// ---------------------------------------------------------------------------
// 纯函数：统计与打卡回顾（移植自 app.py: compute_stats）
// ---------------------------------------------------------------------------
export function computeStats(tasks, today) {
  today = today || todayStr();
  const td = parseDate(today);

  const todayTasks = tasks.filter((t) => t.date === today);
  const todayTotal = todayTasks.length;
  const todayDone = todayTasks.filter((t) => t.done).length;
  const todayRate = todayTotal ? todayDone / todayTotal : 0;

  let weekDone = 0;
  let streak = 0;
  const trend = [];

  if (td) {
    // 本周一 00:00 为起点
    const dow = (td.getDay() + 6) % 7; // 周一=0
    const monday = new Date(td);
    monday.setDate(td.getDate() - dow);
    monday.setHours(0, 0, 0, 0);
    const now = new Date();
    for (const t of tasks) {
      if (!t.done) continue;
      const ca = parseDt(t.completed_at);
      if (ca && ca >= monday && ca <= now) weekDone++;
    }

    // 连续打卡：从今天往前数
    const day = new Date(td);
    while (tasks.some((t) => t.date === isoDate(day) && t.done)) {
      streak++;
      day.setDate(day.getDate() - 1);
    }

    // 近 7 天趋势（按 completed_at 落点）
    for (let i = 6; i >= 0; i--) {
      const d = new Date(td);
      d.setDate(td.getDate() - i);
      const ds = isoDate(d);
      let cnt = 0;
      for (const t of tasks) {
        if (!t.done) continue;
        const ca = parseDt(t.completed_at);
        if (ca && ca.toDateString() === d.toDateString()) cnt++;
      }
      trend.push({ date: ds, done: cnt });
    }
  }

  return {
    today_done: todayDone,
    today_total: todayTotal,
    week_done: weekDone,
    streak,
    today_rate: todayRate,
    trend,
  };
}

// ---------------------------------------------------------------------------
// 存储层（localStorage / 内存回退）
// ---------------------------------------------------------------------------
export function load() {
  try {
    const s = _storage().getItem(TASKS_KEY);
    if (s) return normalizeTasks(JSON.parse(s));
  } catch (_) {}
  return [];
}

export function save(tasks) {
  _storage().setItem(TASKS_KEY, JSON.stringify(tasks || []));
}

export function loadSnaps() {
  try {
    const s = _storage().getItem(SNAPS_KEY);
    return s ? JSON.parse(s) : [];
  } catch (_) {
    return [];
  }
}

export function saveSnaps(snaps) {
  try {
    _storage().setItem(SNAPS_KEY, JSON.stringify((snaps || []).slice(-SNAP_KEEP)));
  } catch (_) {}
}

// 改动「前」存一份当前全量数据（直接服务误删还原）
export function snapshot(tasks) {
  const snaps = loadSnaps();
  snaps.push(JSON.parse(JSON.stringify(tasks)));
  const trimmed = snaps.slice(-SNAP_KEEP);
  saveSnaps(trimmed);
  return trimmed;
}

// 撤销上一次改动：弹出最近一份快照并返还（无快照返回 null）
export function undo() {
  const snaps = loadSnaps();
  if (snaps.length === 0) return null;
  const prev = snaps.pop();
  saveSnaps(snaps);
  return prev;
}

// ---------------------------------------------------------------------------
// 导出（真机 Filesystem+Share；非真机 Blob 下载）
// ---------------------------------------------------------------------------
export function serializeTasks(tasks) {
  return JSON.stringify(tasks, null, 2);
}

export async function exportData(tasks) {
  const json = serializeTasks(tasks);
  // 注意：这里不能用「裸模块名」形式的动态 import（bare specifier）。
  // 网页是未经打包的静态资源，浏览器/WebView 无法解析裸模块名，会直接抛错导致导出功能整个失效。
  // 正确做法：Capacitor 在真机上由原生层把 core 注入到全局 window.Capacitor，
  // 插件则挂在 Capacitor.Plugins 下。桌面浏览器没有这个全局，自然走 Blob 下载分支。
  const Cap = typeof globalThis !== "undefined" ? globalThis.Capacitor : null;
  const isNative = !!(Cap && Cap.isNativePlatform && Cap.isNativePlatform());

  if (isNative) {
    const Filesystem = Cap.Plugins && Cap.Plugins.Filesystem;
    const Share = Cap.Plugins && Cap.Plugins.Share;
    if (!Filesystem || !Share) {
      throw new Error("导出功能所需的插件未加载（Filesystem / Share）");
    }
    const fileName = `tasks-${todayStr()}.json`;
    await Filesystem.writeFile({
      path: fileName,
      data: json,
      directory: "DOCUMENTS",
      recursive: true,
    });
    const uri = await Filesystem.getUri({ path: fileName, directory: "DOCUMENTS" });
    await Share.share({
      title: "导出备份",
      text: "每日任务清单备份",
      files: [uri.uri],
      dialogTitle: "导出备份",
    });
  } else {
    if (typeof document === "undefined") return; // Node 测试环境：静默跳过
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tasks.json";
    a.click();
    URL.revokeObjectURL(url);
  }
}
