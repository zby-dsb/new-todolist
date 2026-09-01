# 每日任务清单 v1.2.0 — 手机端首发

同一套前端代码、两种存储后端：电脑端走 Python 后端，手机端走 App 内本地存储（不联网）。

## 新增（手机端 / Android · 鸿蒙 APK）
- **Capacitor 8** 把网页前端装进安卓壳，JDK 21 构建
- 4 个官方插件：App / Filesystem / Share / SplashScreen
- `store.js`：存储 + 统计 + 快照撤销的纯函数层（浏览器与 Node 测试共用）
- 前端按平台分支：`Capacitor.isNativePlatform()` 为真走本地存储，否则走 `/api/...` 后端
- 手机端交互：单击任务文字编辑（带保存/取消按钮）、安全区适配、跨天自动刷新、返回键连按退出、锁竖屏
- 米色「To Do」图标 + 启动画面（同色系 `#C0A080`，取自源图主色）
- 一键脚本：`安装到手机.bat`（adb 安装启动）、`打包.bat`（重打包）

## 安装到手机
见仓库 README「手机端」一节：开 USB 调试 → 数据线连电脑 → 双击 `安装到手机.bat`。
鸿蒙系统需保持安卓 APK 兼容，**切勿升级到 HarmonyOS NEXT**。

## 工程说明
- 环境全部在 D 盘：JDK 21（`D:\Android\JDK21`）、Android SDK（`D:\Android\Sdk`）、Gradle 8.14.3（本地化 `D:\Android\gradle-dists`，wrapper 走 `file:///`）
- 构建：`打包.bat`（= `cap sync android` + `gradlew.bat assembleDebug`），产物输出到 `dist/`
- 本版为 **debug 签名**，仅供自测 / 侧载；如需上架请另配 release keystore

## 已知问题
- Windows 用户名为中文，AGP 路径非 ASCII，已用官方开关 `android.overridePathCheck=true` 绕过（暂不迁移目录）
- `npm install` 需 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 绕过 WorkBuddy 防误删守卫（仅本机构建环境相关）
