#!/usr/bin/env python3
# coding: utf-8
"""
CapsWriter macOS .app bundle 客户端入口。

职责：
  1. 在主线程初始化 NSApplication，赋予进程 macOS GUI 应用身份。
     这样 macOS 麦克风隐私指示器（菜单栏左侧橙色胶囊）会显示
     "CapsWriter" 而不是 "Python3"。
  2. 写入客户端 PID 文件，供 capswriterd 读取以发送 SIGTERM。
  3. 在子线程运行 CapsWriterClient（asyncio 事件循环）。
  4. 主线程运行 NSApplication RunLoop，保持 GUI 应用身份存活。

进程模型：
  主线程  → NSApplication.run()（RunLoop，提供 macOS 应用身份）
  子线程  → CapsWriterClient.start()（asyncio 事件循环，录音 / WebSocket / 结果处理）

信号处理：
  SIGTERM → 调用 client.stop()（恢复 remap 等清理），移除 PID 文件，退出
  SIGINT  → 双击确认退出（保持原有行为）
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# 项目根目录（本文件位于项目根）
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# PID 文件（供 capswriterd 读取）
# ---------------------------------------------------------------------------
STATE_DIR = Path.home() / '.capswriter' / 'state'
CLIENT_PID_FILE = STATE_DIR / 'client.pid'


def _write_client_pid() -> None:
    """写入当前进程 PID，供 capswriter status 存活检测。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_PID_FILE.write_text(str(os.getpid()))


def _clear_client_pid() -> None:
    """清理 PID 文件。"""
    try:
        CLIENT_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NSApplication 初始化（主线程）
# ---------------------------------------------------------------------------

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory  # noqa: E402
from Foundation import NSObject  # noqa: E402

# 创建 NSApplication 单例，设置为 Accessory 策略（无 Dock 图标、无应用菜单栏）
_nsapp = NSApplication.sharedApplication()
_nsapp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def _set_app_icon() -> None:
    """显式给运行进程设置 app 图标（从 bundle 的 app-icon.icns 读取）。

    为什么需要：launchd 直接 exec agent 二进制启动时，进程的 applicationIconImage
    为空。而 UNUserNotificationCenter 通知横幅左侧图标取的是「发通知进程报告的 app
    图标」，不是磁盘 bundle 图标 —— 于是横幅显示破图占位（同一时刻系统设置→通知里
    读的是磁盘 bundle 图标，反而正常）。显式设上后横幅才会显示 CapsWriter 自己的图标。

    Accessory（LSUIElement）下不显示 Dock 图标，故此调用只影响通知等系统 UI，无副作用。
    """
    try:
        from AppKit import NSImage
        icns = PROJECT_ROOT / 'assets' / 'icon' / 'app-icon.icns'
        img = NSImage.alloc().initWithContentsOfFile_(str(icns))
        if img is not None and img.isValid():
            _nsapp.setApplicationIconImage_(img)
    except Exception:
        pass


_set_app_icon()


def _start_client_thread() -> None:
    """启动 CapsWriterClient 子线程。"""
    t = threading.Thread(target=_run_client, name="CapsWriterClientThread", daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# 菜单栏状态项（NSStatusItem）
#
# 当前阶段：仅显示图标，暂不挂菜单（后续再加菜单内容）。
# 生命周期：状态项由本进程（= 客户端 .app）持有，进程退出（SIGTERM /
#           terminate / os._exit）时 macOS 自动移除，因此图标在菜单栏的
#           存活周期天然与客户端一致，无需手动管理销毁。
# 图标：assets/icon/capswriter-menubar-template.svg（代码正式素材 v2；assets/branding/ 仅放设计/调试件）
#   - 新版 macOS 的 NSImage 原生按矢量（_NSSVGImageRep）渲染，任意倍率清晰；
#   - setTemplate_(True) 让系统按深 / 浅色菜单栏自动反色（只用 alpha）。
# 约束：必须在主线程创建，并保留模块级强引用，防止被 GC 回收导致图标消失。
# ---------------------------------------------------------------------------
_status_item = None
_MENUBAR_ICON = PROJECT_ROOT / 'assets' / 'icon' / 'capswriter-menubar-template.svg'      # 矢量，macOS 13+ 原生
_MENUBAR_ICON_PNG = PROJECT_ROOT / 'assets' / 'icon' / 'capswriter-menubar-template.png'  # @2x 位图，旧系统兜底
_MENUBAR_ICON_HEIGHT = 18.0   # pt：菜单栏图标高度（mark 含约 11% 留白，实际墨迹 ~16pt）
_MENUBAR_AUTOSAVE = "CapsWriterStatusItem"   # 固定标识：macOS 据此持久化用户摆放的位置


def _menubar_dbg(msg: str) -> None:
    """菜单栏诊断日志：追加写入独立文件，避免被 stderr 噪音淹没。"""
    try:
        log = Path.home() / '.capswriter' / 'logs' / 'menubar.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as fp:
            fp.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _load_menubar_image():
    """加载菜单栏图标：优先矢量 SVG（macOS 13+ 原生 _NSSVGImageRep，任意倍率最清晰），
    旧系统（macOS < 13）NSImage 不支持 SVG 时回退到 @2x PNG。
    返回 (NSImage, 来源标记)；都失败返回 (None, None)。"""
    from AppKit import NSImage
    img = NSImage.alloc().initWithContentsOfFile_(str(_MENUBAR_ICON))
    if img is not None and img.isValid():
        return img, 'svg'
    img = NSImage.alloc().initWithContentsOfFile_(str(_MENUBAR_ICON_PNG))
    if img is not None and img.isValid():
        return img, 'png'
    return None, None


# ---------------------------------------------------------------------------
# 菜单栏下拉菜单
#
# 设计：纯 AppKit 原生 NSMenu + NSMenuItem，不使用任何自定义 NSView。
#   这样菜单天然继承系统菜单材质——在最新 macOS（Tahoe/26）上呈现 Liquid Glass
#   半透明效果，并自动跟随系统深色/浅色模式；菜单项图标用 SF Symbol 模板图，
#   同样随外观自动反色。一旦往菜单项塞自定义视图就会破坏这套原生材质，故不这么做。
#
# 菜单结构：
#   ● CapsWriter · 已就绪   （禁用表头，打开时按 ErrorBus 快照刷新文案）
#   ──────────
#   复制最近结果            （无结果时置灰）
#   编辑热词                （open -t hot.txt，系统默认文本编辑器）
#   ──────────
#   重启 CapsWriter         （= capswriter restart）
#   退出 CapsWriter         （= capswriter stop）
# ---------------------------------------------------------------------------
_HOTWORDS_FILE = PROJECT_ROOT / 'hot.txt'
_CAPSWRITER_PY = PROJECT_ROOT / 'capswriter.py'
_VENV_PYTHON = PROJECT_ROOT / '.venv' / 'bin' / 'python'   # 与 install.sh 包装脚本一致

# 菜单相关模块级强引用（防止 PyObjC 对象被 GC 回收）
_status_menu = None
_menu_controller = None
_menu_header_item = None
_menu_copy_item = None


def _format_status_title() -> str:
    """根据 ErrorBus 当前快照生成状态表头文案（菜单打开时刷新）。

    用彩色 emoji 圆点表达状态（color glyph，禁用菜单项也能体现色相）。
    驱动字段为 `ErrorBus.state`——它已把「正常 / 录音 / 引擎断开 / 客户端故障 /
    启动中」编码好，直接据此着色，可天然避开启动期把「权限尚未确认」误报成红灯：
        🟢 运行正常   state=ready（引擎已连 + 键盘接管已建）
        🔵 录音中     state=recording
        🟡 引擎未连接 state=connecting（server 断开 / 连接中）
        🔴 客户端故障 state=error（键盘接管/辅助功能未就绪），或 ready 但麦克风权限缺失
        ⚪️ 启动中     state=starting（初始态）
    """
    eb = _error_bus
    if eb is None:
        return "⚪️ CapsWriter · 启动中…"
    try:
        s = eb.snapshot()
    except Exception:
        return "CapsWriter"
    state = s.get('state')
    if state == 'error':
        return "🔴 CapsWriter · 键盘接管/权限未就绪"
    if state == 'recording':
        return "🔵 CapsWriter · 录音中"
    if state == 'ready':
        if s.get('microphone_ok') is False:
            return "🔴 CapsWriter · 麦克风权限缺失"
        return "🟢 CapsWriter · 运行正常"
    if state == 'connecting':
        return "🟡 CapsWriter · 识别引擎未连接"
    return "⚪️ CapsWriter · 启动中…"


def _recent_text() -> str | None:
    """取最近一次输出文本（优先润色后输出，回退原始识别文本）。"""
    with _client_lock:
        c = _client
    if c is None:
        return None
    st = getattr(c, 'state', None)
    if st is None:
        return None
    return getattr(st, 'last_output_text', None) or getattr(st, 'last_recognition_text', None)


def _sf_symbol_image(name: str):
    """加载一个 SF Symbol 模板图（macOS 11+）；不可用时返回 None。

    SF Symbol 默认是模板图，会随系统深/浅色与菜单材质自动反色，保持原生观感。
    """
    try:
        from AppKit import NSImage
        return NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    except Exception:
        return None


class _StatusMenuController(NSObject):
    """菜单栏下拉菜单的 target + NSMenuDelegate。

    持有各菜单项的动作方法，并在菜单打开前（menuNeedsUpdate:）刷新状态表头
    与「复制最近结果」的可用态。所有动作均非阻塞。
    """

    # ---- NSMenuDelegate：菜单即将显示前刷新动态内容 ----
    def menuNeedsUpdate_(self, menu):
        if _menu_header_item is not None:
            _menu_header_item.setTitle_(_format_status_title())
        if _menu_copy_item is not None:
            _menu_copy_item.setEnabled_(bool(_recent_text()))

    # ---- 动作：复制最近结果到剪贴板 ----
    def copyRecentResult_(self, sender):
        text = _recent_text()
        if not text:
            return
        try:
            from AppKit import NSPasteboard, NSPasteboardTypeString
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(text, NSPasteboardTypeString)
        except Exception as e:
            print(f"[CapsWriter.app] 复制最近结果失败: {e}", file=sys.stderr)

    # ---- 动作：用系统默认文本编辑器打开 hot.txt ----
    def editHotwords_(self, sender):
        import subprocess
        try:
            subprocess.Popen(['open', '-t', str(_HOTWORDS_FILE)])
        except Exception as e:
            print(f"[CapsWriter.app] 打开热词文件失败: {e}", file=sys.stderr)

    # ---- 动作：重启 CapsWriter（= capswriter restart）----
    def restartApp_(self, sender):
        self._run_cli('restart')

    # ---- 动作：退出 CapsWriter（= capswriter stop）----
    def quitApp_(self, sender):
        self._run_cli('stop')

    def _run_cli(self, cmd: str) -> None:
        """detached 派生 `capswriter <cmd>`。

        restart/stop 都会按身份 SIGTERM 杀掉 client（即本进程），因此必须用
        start_new_session=True 让命令脱离本进程的会话独立存活——否则本进程退出后，
        restart 来不及把 client 再拉起来。解释器与 install.sh 包装脚本保持一致。
        """
        import subprocess
        py = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
        try:
            proc = subprocess.Popen(
                [py, str(_CAPSWRITER_PY), cmd],
                cwd=str(PROJECT_ROOT),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # 轻量留痕：记录派生的子进程 PID 与菜单栏自身 PID，便于事后对照双实例时序
            _menubar_dbg(f"spawn capswriter {cmd} -> child pid={proc.pid} (menubar pid={os.getpid()})")
        except Exception as e:
            _menubar_dbg(f"spawn capswriter {cmd} FAILED: {e!r}")
            print(f"[CapsWriter.app] 执行 capswriter {cmd} 失败: {e}", file=sys.stderr)


def _build_status_menu():
    """构建原生 NSMenu 并挂上各项。返回 NSMenu。"""
    from AppKit import NSMenu, NSMenuItem

    global _menu_controller, _menu_header_item, _menu_copy_item

    menu = NSMenu.alloc().init()
    # 关闭自动启停：自行管理各项可用态（表头禁用、复制项按有无结果动态置灰）
    menu.setAutoenablesItems_(False)

    controller = _StatusMenuController.alloc().init()

    def _add(title, action, symbol=None, enabled=True):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        if action is not None:
            item.setTarget_(controller)
        item.setEnabled_(enabled)
        if symbol is not None:
            img = _sf_symbol_image(symbol)
            if img is not None:
                item.setImage_(img)
        menu.addItem_(item)
        return item

    # 状态表头（禁用，打开菜单时刷新文案）
    header = _add(_format_status_title(), None, enabled=False)

    menu.addItem_(NSMenuItem.separatorItem())
    copy_item = _add("复制最近结果", 'copyRecentResult:', symbol='doc.on.clipboard')
    _add("编辑热词", 'editHotwords:', symbol='square.and.pencil')

    menu.addItem_(NSMenuItem.separatorItem())
    _add("重启 CapsWriter", 'restartApp:', symbol='arrow.clockwise')
    _add("退出 CapsWriter", 'quitApp:', symbol='power')

    menu.setDelegate_(controller)

    _menu_controller = controller      # 强引用：target + delegate 不能被回收
    _menu_header_item = header
    _menu_copy_item = copy_item
    return menu


def _install_status_item() -> None:
    """在系统菜单栏创建 CapsWriter 状态项（图标 + 原生下拉菜单）。必须在主线程调用。"""
    global _status_item, _status_menu
    if _status_item is not None:
        return
    try:
        from AppKit import NSStatusBar, NSVariableStatusItemLength
        from Foundation import NSMakeSize

        item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        # autosaveName：让 macOS 按固定标识持久化用户摆放的位置。
        # 用户 ⌘ 拖动图标到喜欢的位置后，重启仍固定在那、不被其它 app 顶掉。
        item.setAutosaveName_(_MENUBAR_AUTOSAVE)

        image, src = _load_menubar_image()
        btn = item.button()
        if image is not None and btn is not None:
            # 等比设高：宽度按 mark 宽高比缩放
            isz = image.size()
            aspect = (isz.width / isz.height) if isz.height else 1.0
            image.setSize_(NSMakeSize(_MENUBAR_ICON_HEIGHT * aspect, _MENUBAR_ICON_HEIGHT))
            image.setTemplate_(True)   # 系统按菜单栏外观（深/浅）自动反色
            btn.setImage_(image)
            btn.setToolTip_("CapsWriter")
            _menubar_dbg(f"ok via {src}")
        else:
            # 图标都加载失败时用文字兜底，至少能看到状态项
            if btn is not None:
                btn.setTitle_("CW")
            _menubar_dbg("icon load failed -> text fallback 'CW'")
            print(f"[CapsWriter.app] 菜单栏图标加载失败: {_MENUBAR_ICON} / {_MENUBAR_ICON_PNG}", file=sys.stderr)

        # 挂上原生下拉菜单（设置 menu 后，左键点击图标即弹出）
        try:
            _status_menu = _build_status_menu()
            item.setMenu_(_status_menu)
        except Exception as e:
            _menubar_dbg(f"menu build EXCEPTION {e!r}")
            print(f"[CapsWriter.app] 构建菜单失败（仅图标可用）: {e}", file=sys.stderr)

        _status_item = item   # 强引用，防止被回收
    except Exception as e:
        _menubar_dbg(f"EXCEPTION {e!r}")
        print(f"[CapsWriter.app] 创建菜单栏状态项失败: {e}", file=sys.stderr)


class _AppDelegate(NSObject):
    """NSApplication 代理，处理应用生命周期事件。"""

    def applicationDidFinishLaunching_(self, notification):
        """启动完成后：先通过 AVFoundation 确保麦克风权限已处理，再启动客户端。
        PortAudio/sounddevice 直接调 CoreAudio，不会触发 TCC 弹窗；
        必须走 AVFoundation API 才能让 macOS 弹出授权对话框。
        """
        # 先在菜单栏挂出图标（主线程），使其与客户端 .app 同生命周期出现
        _install_status_item()

        try:
            import AVFoundation as _avf
            status = _avf.AVCaptureDevice.authorizationStatusForMediaType_(
                _avf.AVMediaTypeAudio
            )
            # AVAuthorizationStatusNotDetermined = 0：尚未询问，弹窗请求
            if status == 0:
                def _on_mic_permission(granted):
                    if not granted:
                        print("[CapsWriter.app] 麦克风权限被用户拒绝", file=sys.stderr)
                    _start_client_thread()
                _avf.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    _avf.AVMediaTypeAudio, _on_mic_permission
                )
                return  # 等待用户响应后由回调启动客户端
        except Exception as e:
            print(f"[CapsWriter.app] AVFoundation 权限请求失败，直接启动: {e}", file=sys.stderr)

        # 权限已确定（授权/拒绝/受限）或 AVFoundation 不可用，直接启动
        _start_client_thread()

    def applicationWillTerminate_(self, notification):
        """NSApplication 即将退出时的清理回调。"""
        _critical_cleanup()
        # 不再 return，os._exit(0) 已在 _critical_cleanup 里调用


# ---------------------------------------------------------------------------
# 客户端引用（跨线程共享）
# ---------------------------------------------------------------------------
_client = None
_client_lock = threading.Lock()
_error_bus = None   # ErrorBus 实例，由 _run_client() 创建后存入


def _cleanup() -> None:
    """统一清理：停止客户端 + 移除 PID 文件 + 删除 status.json。"""
    global _client, _error_bus
    with _client_lock:
        if _client is not None:
            try:
                _client.stop()
            except Exception:
                pass
            _client = None
    _clear_client_pid()
    if _error_bus is not None:
        _error_bus.cleanup()   # 删除 status.json，确保 capswriter status 不显示陈旧数据
        _error_bus = None


# ---------------------------------------------------------------------------
# 信号处理（必须在主线程注册）
#
# 问题：NSApp.run() 在主线程占用 C 级别 RunLoop，Python 的 signal handler 无法
# 在此期间执行（handler 在 Python bytecode 之间检查，而主线程陷在 C 代码里）。
#
# 解法：set_wakeup_fd() + SigtermWatcher 守护线程
#   1. signal.set_wakeup_fd(_sig_w) 让 Python 在 C 级别将信号编号写入管道
#      （async-signal-safe，不依赖主线程执行 Python 代码）
#   2. SigtermWatcher 守护线程阻塞在 os.read(_sig_r)，收到 SIGTERM 字节后
#      立即执行关键清理并 os._exit(0)
# ---------------------------------------------------------------------------

_last_sigint_time = 0.0

# 管道：写端供 set_wakeup_fd，读端供 SigtermWatcher 线程
_sig_r, _sig_w = os.pipe()
os.set_blocking(_sig_w, False)   # set_wakeup_fd 要求写端非阻塞


def _critical_cleanup() -> None:
    """最小化关键清理：恢复 Caps Lock remap、删除 PID 文件和 status.json，然后 os._exit(0)。

    不做音频流/WebSocket 等可能挂起的清理；OS 在进程退出后自动回收所有资源。
    必须用 os._exit(0)（不是 sys.exit），确保 launchd 看到 exit 0，不触发重启。
    """
    # 看门狗：清理挂住时 2 秒后强制退出。
    # CGEventTapEnable 在 tap 坏态（AX 被撤）下可能死锁，导致 os._exit 永远走不到、
    # 进程变僵尸 → 双实例。看门狗保证 SIGTERM 后进程必定在 2s 内退出。
    def _watchdog():
        time.sleep(2.0)
        # 必须 os._exit(0)：非零退出会被 launchd KeepAlive(SuccessfulExit=false) 当成崩溃
        # 而复活进程 → 与显式 start / 被领养的孤儿相撞 → 双实例。看门狗的职责只是「保证
        # 进程一定退出」，而非「报告失败」，所以这里同样用 0。
        os._exit(0)
    threading.Thread(target=_watchdog, daemon=True, name="CleanupWatchdog").start()

    global _client, _error_bus
    # 最关键：恢复 Caps Lock remap（hidutil 持久化系统状态，进程退出后不自动恢复）
    with _client_lock:
        c = _client
        _client = None
    if c is not None:
        try:
            c.stop_platform_shortcut_bridge()
        except Exception:
            pass
        if getattr(c, 'remap_session', None) is not None:
            try:
                c.remap_session.restore()  # 内部有双实例 PID 保护，不会覆盖新实例的 remap
            except Exception:
                pass
    # 清理进程级文件
    _clear_client_pid()
    eb = _error_bus
    _error_bus = None
    if eb is not None:
        try:
            eb.cleanup()
        except Exception:
            pass
    os._exit(0)


def _sigterm_watcher() -> None:
    """守护线程：阻塞读取信号管道，收到 SIGTERM 后立即执行关键清理并退出。"""
    while True:
        try:
            data = os.read(_sig_r, 64)
        except OSError:
            return
        if signal.SIGTERM in data:
            _critical_cleanup()


def _on_sigterm(signum, frame) -> None:
    """Python 级 SIGTERM 备用处理器。

    正常情况下 SigtermWatcher 线程通过 set_wakeup_fd 更快触发。
    若主线程未被 NSApp 占用（如直接 python 命令行调试），此处理器也能工作。
    """
    _critical_cleanup()


def _on_sigint(signum, frame):
    """SIGINT：双击确认退出（交互场景，保持原有行为）。"""
    global _last_sigint_time
    now = time.time()
    if now - _last_sigint_time > 1.0:
        _last_sigint_time = now
        print(f"\n收到 {signal.Signals(signum).name}，1秒内再次按下将会退出...")
    else:
        print(f"\n收到 {signal.Signals(signum).name}，确认退出...\n")
        _cleanup()
        sys.exit(0)


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigint)
# set_wakeup_fd：SIGTERM 到达时在 C 级别写管道字节，唤醒 SigtermWatcher 线程
signal.set_wakeup_fd(_sig_w)

# 启动 SIGTERM 守护线程（必须在 set_wakeup_fd 之后）
threading.Thread(target=_sigterm_watcher, daemon=True, name="SigtermWatcher").start()


# ---------------------------------------------------------------------------
# 客户端子线程
# ---------------------------------------------------------------------------

def _run_client() -> None:
    """在子线程运行 CapsWriterClient。"""
    global _client, _error_bus
    try:
        from core.client.app import CapsWriterClient
        from core.client.error_bus import ErrorBus

        # 创建 ErrorBus，写入 connecting 初始状态（server 此时可能尚未连接）
        eb = ErrorBus()
        eb.update(state='connecting')
        _error_bus = eb

        # 创建 client 并注入 ErrorBus
        client = CapsWriterClient(error_bus=eb)
        with _client_lock:
            _client = client

        # 麦克风权限已由 applicationDidFinishLaunching_ 确认，更新状态
        eb.update(microphone_ok=True)

        # register_signals=False：信号已在主线程处理，子线程不可调用 signal.signal()
        client.start(register_signals=False)
    except Exception as e:
        # 输出完整 traceback，方便诊断崩溃原因
        print(f"[CapsWriter.app] 客户端异常退出: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    finally:
        # 客户端退出后，通知 NSApplication 终止
        _nsapp.performSelectorOnMainThread_withObject_waitUntilDone_(
            'terminate:', None, False
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    # 写入 PID 文件
    _write_client_pid()
    atexit.register(_clear_client_pid)

    # 设置 NSApplication 代理
    delegate = _AppDelegate.alloc().init()
    _nsapp.setDelegate_(delegate)

    # 客户端线程由 applicationDidFinishLaunching_ 在确认麦克风权限后启动

    # 主线程运行 NSApplication RunLoop（阻塞）
    from PyObjCTools import AppHelper
    AppHelper.runEventLoop()

    # RunLoop 退出后清理
    _cleanup()
    return 0


if __name__ == '__main__':
    sys.exit(main())
