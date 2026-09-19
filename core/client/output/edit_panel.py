# coding: utf-8 -*-
"""
macOS 原生编辑框面板（PyObjC，2026-08-23 创建，2026-08-24 UI 重做）

编辑框模式：Caps 长按识别完成后，结果先进本面板；Enter=确认（先恢复按下 Caps
时的前台应用并粘贴上屏，再做音频/日记/标注持久化），Esc=放弃（不入标注库；
非空转录写剪贴板但不自动上屏，由 result_processor 落实）。面板打开期间
shortcut_manager 抑制新录音触发。

UI（2026-08-24 口径）：面板基本只有一个编辑框——borderless 窗口 + 毛玻璃圆角
底（NSVisualEffectView）；NSTextView 按宽度自动换行（自动换行与模型输出中的
换行符不做额外标注，Word 式逻辑）；Enter=确认，Shift+Enter=手动插入换行，Tab
吞掉防止焦点跳出；高度随内容自适应（底边锚定向上生长，上限为屏幕可视高度
40%，超出后在同一编辑框内滚动；面板水平居中、整体靠上，上边缘固定，内容
增加时向下伸展、减少时从下边缩回）。

线程模型：AppKit 面板必须在主线程创建与操作；本模块经 AppHelper.callAfter 把
显示/激活动作派发到主线程。确认/取消回调由 result_processor 提供，内部用
asyncio.run_coroutine_threadsafe 回客户端事件循环，因此主线程调用它们是安全
的；取消回调带回面板当前文本（on_cancel(text)）。AppKit 导入失败（非 macOS/
无 PyObjC）时所有入口安全降级。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

try:
    from AppKit import (
        NSEvent, NSEventModifierFlagShift,
        NSPanel, NSView, NSScrollView, NSTextView, NSFont,
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered, NSFloatingWindowLevel,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSVisualEffectView, NSVisualEffectMaterialPopover,
        NSVisualEffectBlendingModeBehindWindow, NSVisualEffectStateActive,
        NSColor, NSFocusRingTypeNone,
    )
    from Foundation import NSObject, NSRect, NSPoint, NSSize, NSRange, NSNotFound
    from PyObjCTools import AppHelper
    import objc
    # 本机 pyobjc 未导出 SwiftUI 时代的 NSWindowLevelFloating 常量名，
    # 使用等价经典常量 NSFloatingWindowLevel（值同为 3）
    NSWindowLevelFloating = NSFloatingWindowLevel
    _APPKIT_OK = True
except Exception:  # 非 macOS / PyObjC 缺失：整体功能静默关闭
    _APPKIT_OK = False

import logging
logger = logging.getLogger(__name__)

_PANEL_W = 680            # 面板宽（固定，只做高度自适应）
_MARGIN = 10              # 文本框距面板边缘
_CORNER_RADIUS = 12.0     # 面板圆角；裁剪容器与毛玻璃材质必须保持一致
_MIN_TEXT_H = 42          # 单行时的文本框高
_MAX_SCREEN_RATIO = 0.4   # 面板最高占屏幕可视高度的比例
_PANEL_TOP_RATIO = 0.70   # 面板上边缘位于可视区高度 70%，水平居中、整体靠上
_controller = None          # EditorPanelController 单例（主线程创建）
_active = False             # 面板是否打开（主线程读写，读侧仅作提示用途）
_pending = False            # present_editor 已派发但 show_panel 尚未执行（跨线程窗口）
_pending_since = None       # _pending 置位时刻（time.monotonic()），用于看门狗判定派发丢失
_PENDING_TIMEOUT = 5.0      # 秒：超过视为 callAfter 派发丢失，自动复位
_ACTIVATE_TIMEOUT = 0.08    # 秒：仅在目标应用尚未激活时短暂轮询，不再固定等待 250ms
_ACTIVATE_POLL = 0.005      # 秒：检测到目标已激活就立即继续上屏


def editor_command(selector: str, *, shift_pressed: bool = False) -> Optional[str]:
    """将 NSTextView selector 归为稳定的编辑框产品命令。

    此函数故意不接触 AppKit 实例，供离线测试锁定 Enter、Shift+Enter、Esc 与 Tab
    的语义。NSTextView 的 selector 不能可靠表达物理修饰键：标准键绑定中
    `insertLineBreak:`/`insertNewlineIgnoringFieldEditor:` 分别可对应 Control/Option
    组合。因此只以显式传入的 Shift 状态决定 `insertNewline:` 是确认还是手动换行。
    """
    if selector == 'insertNewline:':
        # 编辑框产品口径只定义 Enter 与 Shift+Enter：同一 selector 下必须查看
        # 物理 Shift 状态，不能把 Option/Control 对应的其它 selector 误当 Shift。
        return 'newline' if shift_pressed else 'confirm'
    commands = {
        'cancelOperation:': 'cancel',
        'complete:': 'cancel',
        'insertTab:': 'tab',
        'insertBacktab:': 'tab',
    }
    return commands.get(selector)


def panel_origin_y(
    content_height: float,
    screen_origin_y: float,
    screen_height: float,
    top_ratio: float = 0.70,
) -> float:
    """按固定上边缘计算面板底边：内容增高时只向下伸展，缩短时从下边收回。"""
    top_y = screen_origin_y + screen_height * top_ratio
    return max(screen_origin_y, top_y - content_height)


def is_available() -> bool:
    return _APPKIT_OK


def is_active() -> bool:
    # 同时覆盖 _pending：present_editor 在工作线程返回 True 后、主线程 show_panel
    # 置 _active 前存在一个竞态窗口，期间也应视为「面板打开」以抑制新录音。
    global _pending, _pending_since
    if _pending:
        # 看门狗：callAfter 派发若丢失（主线程 RunLoop 异常等），_pending 会永久
        # 卡 True 导致录音被永久静默抑制。超时后自动复位，宁可误弹一次面板
        # 的竞态窗口，也不让录音永久失效。
        if _pending_since is not None and \
                (time.monotonic() - _pending_since) > _PENDING_TIMEOUT:
            _pending = False
            _pending_since = None
            logger.warning("[edit-panel] present 派发疑似丢失，自动复位 pending 标志")
    return _active or _pending


def capture_frontmost_app() -> Optional[dict]:
    """在按下 Caps 的时刻调用：记录前台应用，作为稍后恢复焦点/上屏的目标。"""
    if not _APPKIT_OK:
        return None
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return {
            'pid': int(app.processIdentifier()),
            'bundle_id': app.bundleIdentifier(),
            'name': app.localizedName(),
        }
    except Exception as e:
        logger.debug(f"capture_frontmost_app 失败: {e}")
        return None


def init_panel() -> bool:
    """创建面板控制器。必须在主线程、NSApp 启动后调用一次（start_client_macos）。"""
    global _controller
    if not _APPKIT_OK:
        return False
    if _controller is not None:
        return True
    _controller = EditorPanelController.alloc().init()
    return _controller is not None


def present_editor(text: str, on_confirm: Callable[[str], None],
                   on_cancel: Callable[[str], None]) -> bool:
    """线程安全入口：显示编辑框并预填识别文本。返回 False 时调用方回退直接上屏。

    on_cancel(text) 带回面板当前文本：Esc=放弃，但非空转录仍要写剪贴板（不上屏）。
    """
    global _pending, _pending_since
    if not _APPKIT_OK or _controller is None or _active or _pending:
        return False
    _pending = True
    _pending_since = time.monotonic()
    AppHelper.callAfter(_controller.show_panel, text, on_confirm, on_cancel)
    return True


def activate_app_sync(target: Optional[dict]) -> None:
    """请求恢复目标应用；检测到激活即返回，最多等待 80ms 异常兜底。"""
    if not _APPKIT_OK or not target:
        return
    pid = int(target.get('pid', -1))
    if pid <= 0:
        return
    try:
        from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None or app.isTerminated():
            logger.info(f"[editor] 目标应用已退出（pid={pid}），跳过恢复焦点")
            return
        AppHelper.callAfter(
            app.activateWithOptions_, NSApplicationActivateIgnoringOtherApps)
        # AppHelper 只负责把激活动作送到主线程。旧实现无条件 sleep 250ms，
        # 即使应用已瞬间激活也要干等；现在只在尚未 active 时按 5ms 轮询，
        # 正常情况检测到目标已接管焦点就立即返回。
        deadline = time.monotonic() + _ACTIVATE_TIMEOUT
        while not app.isActive() and time.monotonic() < deadline:
            time.sleep(_ACTIVATE_POLL)
    except Exception as e:
        logger.warning(f"[editor] 恢复目标应用焦点失败: {e}")


if _APPKIT_OK:

    class EditorPanel(NSPanel):
        """borderless 面板：默认 NSPanel 不能成为 key window，必须显式放开，
        否则 makeFirstResponder 无效、文本框收不到键盘输入。"""

        def canBecomeKeyWindow(self):
            return True

    class EditorPanelController(NSObject):
        """NSPanel 持有者 + NSTextView 委托（Enter 确认 / Shift+Enter 换行 / Esc 放弃）。"""

        def init(self):
            self = objc.super(EditorPanelController, self).init()
            frame = NSRect(NSPoint(0, 0), NSSize(_PANEL_W, _MIN_TEXT_H + 2 * _MARGIN))
            # borderless：无标题栏无红叉，面板=一个毛玻璃圆角编辑框；
            # 置顶浮动 + 可加入所有空间（含全屏旁路），与输入法候选框同级交互模型
            self.panel = EditorPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                frame,
                NSWindowStyleMaskBorderless,
                NSBackingStoreBuffered, False)
            self.panel.setOpaque_(False)
            self.panel.setBackgroundColor_(NSColor.clearColor())
            self.panel.setHasShadow_(True)
            self.panel.setLevel_(NSWindowLevelFloating)
            self.panel.setHidesOnDeactivate_(False)
            self.panel.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary)
            self.panel.setReleasedWhenClosed_(False)
            self.panel.setDelegate_(self)

            # 透明圆角裁剪容器必须独立于 NSVisualEffectView。若直接把毛玻璃设为
            # NSPanel 根内容视图，macOS 会让 visual-effect 的私有背板仍按矩形窗口
            # 参与合成，仅设置 effect.layer.cornerRadius 会在四角露出白色矩形底。
            # 外层容器建立第二道合成边界，保证材质、窗口底色和阴影都以同一个
            # 圆角轮廓输出。
            self.clip_view = NSView.alloc().initWithFrame_(
                NSRect(NSPoint(0, 0), NSSize(_PANEL_W, _MIN_TEXT_H + 2 * _MARGIN)))
            self.clip_view.setWantsLayer_(True)
            self.clip_view.layer().setCornerRadius_(_CORNER_RADIUS)
            self.clip_view.layer().setMasksToBounds_(True)
            self.panel.setContentView_(self.clip_view)

            # 毛玻璃圆角底（popover 材质，随系统深浅色自适应）。effect 自身也
            # 保留圆角裁剪，避免其私有材质层在外层容器建立合成前短暂闪出直角。
            self.effect = NSVisualEffectView.alloc().initWithFrame_(
                NSRect(NSPoint(0, 0), NSSize(_PANEL_W, _MIN_TEXT_H + 2 * _MARGIN)))
            self.effect.setMaterial_(NSVisualEffectMaterialPopover)
            self.effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            self.effect.setState_(NSVisualEffectStateActive)
            self.effect.setWantsLayer_(True)
            self.effect.layer().setCornerRadius_(_CORNER_RADIUS)
            self.effect.layer().setMasksToBounds_(True)
            self.clip_view.addSubview_(self.effect)

            # 多行纯文本编辑器：按宽度自动换行。高度受上限时，裸 NSTextView 会
            # 裁切超出的行且没有滚动视口，故由无边框 NSScrollView 承载；滚动条
            # 自动隐藏，短文本视觉上仍然只有一个编辑框，不增加常驻可见控件。
            self.scroll_view = NSScrollView.alloc().initWithFrame_(
                NSRect(NSPoint(_MARGIN, _MARGIN),
                       NSSize(_PANEL_W - 2 * _MARGIN, _MIN_TEXT_H)))
            self.scroll_view.setBorderType_(0)
            self.scroll_view.setDrawsBackground_(False)
            self.scroll_view.setHasHorizontalScroller_(False)
            self.scroll_view.setHasVerticalScroller_(True)
            self.scroll_view.setAutohidesScrollers_(True)
            self.text_view = NSTextView.alloc().initWithFrame_(
                NSRect(NSPoint(0, 0),
                       NSSize(_PANEL_W - 2 * _MARGIN, _MIN_TEXT_H)))
            self.text_view.setRichText_(False)
            self.text_view.setUsesFontPanel_(False)
            self.text_view.setUsesRuler_(False)
            self.text_view.setAllowsUndo_(True)
            self.text_view.setEditable_(True)
            self.text_view.setSelectable_(True)
            self.text_view.setDrawsBackground_(False)
            self.text_view.setFont_(NSFont.systemFontOfSize_(16))
            self.text_view.setTextContainerInset_((6.0, 9.0))
            self.text_view.setFocusRingType_(NSFocusRingTypeNone)
            container = self.text_view.textContainer()
            container.setWidthTracksTextView_(True)   # 容器宽跟随视图 -> 自动换行
            container.setHeightTracksTextView_(False)
            # 容器高给足：让 layoutManager 完整排版，用 usedRect 反推所需高度
            container.setContainerSize_(NSSize(_PANEL_W - 2 * _MARGIN - 12.0, 1.0e7))
            self.text_view.setDelegate_(self)
            self.scroll_view.setDocumentView_(self.text_view)
            self.effect.addSubview_(self.scroll_view)

            self._on_confirm = None
            self._on_cancel = None
            return self

        # ---- 显示/关闭（全部主线程）----
        def show_panel(self, text, on_confirm, on_cancel):
            global _active, _pending, _pending_since
            _pending = False  # 派发已到达主线程，窗口关闭
            _pending_since = None
            if _active:
                return
            _active = True
            self._on_confirm = on_confirm
            self._on_cancel = on_cancel
            try:
                self.text_view.setString_(text)
                self._resize_panel()  # 按内容定高；几何函数固定上边缘、向下伸缩
                # 水平居中，垂直整体靠上；高度变化不再移动上边缘。
                from AppKit import NSScreen
                screen = NSScreen.mainScreen().visibleFrame()
                x = screen.origin.x + (screen.size.width - _PANEL_W) / 2
                y = panel_origin_y(
                    self.panel.frame().size.height,
                    screen.origin.y,
                    screen.size.height,
                )
                self.panel.setFrameOrigin_(NSPoint(x, y))
                from AppKit import NSApplication, NSApp
                NSApp.activateIgnoringOtherApps_(True)
                self.panel.makeKeyAndOrderFront_(None)
                self.panel.makeFirstResponder_(self.text_view)
                self.text_view.setSelectedRange_(NSRange(0, len(text)))  # 全选：整段替换/局部改
            except Exception:
                # _active 已置位但面板未真正显示：回落清理，避免永久抑制录音。
                # 不调用任何回调：面板从未成功展示给用户，确认（上屏）与放弃
                # （写剪贴板）都基于「用户看过面板」这一前提，此时静默丢弃本次
                # 结果（用户重新口述）最安全。
                logger.error("[edit-panel] show_panel 异常，回落清理面板状态",
                             exc_info=True)
                self._on_confirm = None
                self._on_cancel = None
                self._dismiss()

        def _dismiss(self):
            global _active
            _active = False
            self.panel.orderOut_(None)

        def _confirm(self):
            final = str(self.text_view.string())
            cb = self._on_confirm
            self._on_confirm = self._on_cancel = None
            self._dismiss()
            if cb is not None:
                # 退出竞态下 asyncio.run_coroutine_threadsafe 可能抛 RuntimeError
                # （事件循环已关闭），不能让它穿透 PyObjC 委托打断 AppKit 主线程
                try:
                    cb(final)
                except Exception:
                    logger.error("[edit-panel] 确认回调异常（忽略）", exc_info=True)

        def _cancel(self):
            # 放弃也带回面板当前文本：调用方据此决定是否写剪贴板（非空才写）
            text = str(self.text_view.string())
            cb = self._on_cancel
            self._on_confirm = self._on_cancel = None
            self._dismiss()
            if cb is not None:
                # 同上：取消回调异常不外抛，仅留痕
                try:
                    cb(text)
                except Exception:
                    logger.error("[edit-panel] 取消回调异常（忽略）", exc_info=True)

        # ---- 高度自适应（主线程）----
        def _resize_panel(self):
            """按当前内容重算高度；固定上边缘，窗口只向下伸展或从下边缩回。"""
            try:
                from AppKit import NSScreen
                lm = self.text_view.layoutManager()
                container = self.text_view.textContainer()
                lm.glyphRangeForTextContainer_(container)
                used = lm.usedRectForTextContainer_(container)
                inset = self.text_view.textContainerInset()
                text_h = used.size.height + 2 * inset.height

                screen = NSScreen.mainScreen().visibleFrame()
                max_content_h = screen.size.height * _MAX_SCREEN_RATIO
                visible_text_h = max(
                    _MIN_TEXT_H, min(text_h, max_content_h - 2 * _MARGIN))

                tv_w = _PANEL_W - 2 * _MARGIN
                # 文档视图保持完整内容高度，才能由 NSScrollView 在达到窗口上限后
                # 提供可达的垂直滚动，而不是把超出部分截断在视口外。
                self.text_view.setFrame_(
                    NSRect(NSPoint(0, 0), NSSize(tv_w, max(text_h, visible_text_h))))
                self.scroll_view.setFrame_(
                    NSRect(NSPoint(_MARGIN, _MARGIN), NSSize(tv_w, visible_text_h)))
                content_h = visible_text_h + 2 * _MARGIN
                origin = self.panel.frame().origin
                y = panel_origin_y(
                    content_h, screen.origin.y, screen.size.height)
                self.panel.setFrame_display_(
                    NSRect(NSPoint(origin.x, y), NSSize(_PANEL_W, content_h)), True)
                # NSPanel 会自动调整根内容视图，但其子级 effect 不会随窗口缩放；
                # 每次高度变化后同步铺满裁剪容器，防止底部留下未覆盖的透明带。
                self.effect.setFrame_(self.clip_view.bounds())
            except Exception:
                logger.debug("[edit-panel] 高度自适应失败（忽略，维持当前尺寸）",
                             exc_info=True)

        # ---- NSTextView 委托：Enter/Esc/Shift+Enter ----
        def textDidChange_(self, notification):
            # 内容变化（含 Shift+Enter 插入的显式换行）后重算高度
            self._resize_panel()

        def textView_doCommandBySelector_(self, tv, selector):
            # 委托只消费 editor_command 的分类结果，按键 selector 的平台差异集中在
            # 纯函数内。selector 无法证明 Shift 是否按下，故从当前 AppKit 事件读取
            # 物理修饰状态并显式传入，确保 Shift+Enter 不会落入普通 Enter 的确认。
            shift_pressed = bool(NSEvent.modifierFlags() & NSEventModifierFlagShift)
            command = editor_command(str(selector), shift_pressed=shift_pressed)
            if command == 'confirm':
                # 纯 Enter = 确认编辑并上屏
                self._confirm()
                return True
            if command == 'newline':
                # Shift+Enter = 手动插入换行（显式换行，区别于宽度自动换行）
                tv.insertText_replacementRange_('\n', NSRange(NSNotFound, 0))
                self._resize_panel()
                return True
            if command == 'cancel':
                # Esc = 放弃（不入标注库；非空转录写剪贴板不上屏，由回调落实）
                self._cancel()
                return True
            if command == 'tab':
                # 吞掉 Tab：避免焦点跳出到别的控件
                return True
            return False

        # ---- NSWindow 委托：窗口被关闭兜底 = 放弃 ----
        def windowWillClose_(self, notification):
            if _active:
                self._cancel()
