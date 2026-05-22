# Bug Report：macOS 麦克风指示器显示 "Python3" 而非 "CapsWriter"

**日期**：2026-05-20  
**分支**：`mac-dev`  
**优先级**：P2

---

## PROBLEM（问题描述）

将 Python 客户端封装为 `CapsWriter.app` bundle 后，录音期间**菜单栏左侧不出现橙色麦克风胶囊**；Control Center 内有橙色麦克风点但显示使用方为 "Python3" 而非 "CapsWriter"。同 Mac 上其他 GUI 应用（系统录音机、微信等）均能正常触发菜单栏胶囊。

---

## SYSTEM（环境信息）

- OS：macOS 26.4 (Build 25E246), Apple Silicon (arm64)
- 语言 / 运行时：Python 3.13.13（mise 管理），PyObjC 11.x
- 关键依赖：`pyobjc-framework-Cocoa`、`sounddevice`（CoreAudio 后端）、`websockets`
- 相关工具链：clang (Xcode Command Line Tools)，Launch Services（`open -W -n`）

---

## TRIED（已尝试的方法）

1. **用 shell 脚本作为 CFBundleExecutable** → `open` 返回 `-10669 (kLSApplicationNotPermittedToExecuteError)`，app 完全无法启动
2. **编译最小 Mach-O C launcher，用 `execv` 替换为 Python** → app 正常启动、录音功能正常，但 Control Center 显示 "Python3"，菜单栏无橙色胶囊
3. **在 `start_client_macos.py` 中调用 `NSApplication.sharedApplication()` 并设置 `NSApplicationActivationPolicyAccessory`** → NSApp RunLoop 运行正常，Input Monitoring 权限弹窗以 "CapsWriter" 名义弹出，但麦克风归属仍为 "Python3"

---

## CONFIRMED FACTS（已确认的事实）

- `CapsWriter.app/Contents/MacOS/CapsWriter` 是合法 Mach-O arm64 二进制（`file` 验证）✓
- `Info.plist` 包含 `CFBundleIdentifier = com.capswriter.client`、`NSMicrophoneUsageDescription`、`LSUIElement = true` ✓
- 无 quarantine xattr，可执行权限 0755 ✓
- **Input Monitoring TCC 弹窗以 "CapsWriter" 显示**（Launch Services bundle 注册生效）✓
- **Microphone TCC 在 Control Center 显示 "Python3"**（CoreAudio 访问时 TCC 用的是 execv 后的进程身份）
- 同 Mac 其他 GUI 应用（含系统录音机）录音时菜单栏橙色胶囊正常出现
- mise Python 3.13.13：`Py_ENABLE_SHARED=1`，`libpython3.13.dylib` 位于 `~/.local/share/mise/installs/python/3.13.13/lib/`

---

## KEY HYPOTHESIS（核心假设，待验证）

> `execv(python, ...)` 在替换进程镜像后，TCC 对 CoreAudio/麦克风的归属追踪从 Launch Services 的 bundle 注册切换为**当前可执行文件的代码签名身份**（即 Python binary），导致 Input Monitoring（由 LaunchServices 绑定）与 Microphone（由 CoreAudio 访问时实时判断）出现分裂。

### 假设审计

| 假设 | 是否已验证 | 如果错了，意味着什么 |
|------|------------|----------------------|
| TCC 麦克风归属用当前进程可执行文件身份，而非 LaunchServices 注册的 bundle ID | 部分（现象一致，未查文档确认） | 可能有其他原因导致 Python3 显示，修复方向完全不同 |
| `execv` 会丢失 LaunchServices 的 bundle 关联 | 部分（Input Monitoring 未丢失，Microphone 丢失，说明两者机制不同） | 也许 execv 后 bundle 关联仍在，问题出在 PyObjC/sounddevice 的初始化 |
| 不使用 `execv`（C launcher 常驻，内嵌 Python via libpython）可以修复 | **未验证** | 如果 TCC 用的是 audit token 而非代码签名，内嵌 Python 也无效 |
| macOS 26 橙色胶囊仍在菜单栏（用户确认其他 app 有） | ✅ 已确认 | — |

---

## QUESTIONS FOR RESEARCH（需要另一个 Agent 搜索的问题）

1. **macOS TCC 对麦克风访问的进程身份追踪机制是什么？** 是用 `SecCodeRef`（代码签名）、`audit token`、还是 LaunchServices 注册的 bundle ID？`execv` 后哪些信息会变、哪些会继承？
2. **`open -W -n App.app` 启动的进程，在 `execv` 替换可执行文件后，LaunchServices 的 bundle 注册是否仍与该 PID 绑定？** 有无官方文档或 WWDC session 说明？
3. **用 `dlopen` 加载 `libpython3.13.dylib` 并在 C launcher 内调用 `Py_RunMain()`（不 execv），TCC 会以 C launcher（CapsWriter 身份）归属麦克风使用吗？** 有无已知案例或 py2app 源码可参考？
4. **`NSMicrophoneUsageDescription` + `NSApplication` 初始化是否足以触发 TCC 麦克风权限弹窗并关联 bundle？** 还是必须通过 `AVCaptureDevice.requestAccess(for:)` 之类的 API 显式请求？
5. **py2app 生成的 .app bundle 是如何处理 Python 进程身份与 TCC 归属的？** 它是否保持 C launcher 作为主进程？

---

## EXPECTED OUTCOME（期望从报告中获得什么）

- 确认 `execv` 后 TCC 麦克风归属的具体机制（代码签名 vs LaunchServices bundle）
- 给出最简修复路径：是"C launcher 内嵌 libpython"就能解决，还是需要代码签名/entitlements/其他机制
- 如果内嵌 libpython 可行，给出 `Py_RunMain` 的最小可用 C 代码模板（需设置 venv PYTHONHOME/PYTHONPATH）

---

## 当前代码结构参考

```
CapsWriter.app/
├── Contents/
│   ├── Info.plist          # CFBundleIdentifier=com.capswriter.client, LSUIElement=true
│   └── MacOS/
│       ├── CapsWriter      # Mach-O C launcher（当前用 execv 替换为 Python）
│       └── launcher.c      # 源码

start_client_macos.py       # Python 入口：NSApplication 主线程 + asyncio 子线程
capswriterd.py              # 守护进程：open -W -n CapsWriter.app 启动 client
```

`launcher.c` 核心逻辑（当前）：

```c
// 从 exe 路径向上 4 级找项目根，构造 python 路径和脚本路径
snprintf(python_path, sizeof(python_path), "%s/.venv/bin/python", project_root);
snprintf(entry_path,  sizeof(entry_path),  "%s/start_client_macos.py", project_root);
char *new_argv[] = {python_path, entry_path, NULL};
execv(python_path, new_argv);  // ← 问题所在：替换进程后 TCC 身份变为 Python
```
