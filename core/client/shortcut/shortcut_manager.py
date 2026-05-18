# coding: utf-8
"""
快捷键管理器（重构版）

统一管理多个快捷键，处理键盘和鼠标事件，支持：
1. 多快捷键并发处理
2. 防止不同按键互相干扰
3. restore 功能的防自捕获逻辑
4. hold_mode 和 click_mode 支持
"""
from __future__ import annotations
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Dict, List, Optional

from pynput import keyboard, mouse

from . import logger
from core.client.shortcut.key_mapper import *
from core.client.shortcut.key_mapper import KeyMapper
from core.client.shortcut.emulator import ShortcutEmulator
from core.client.shortcut.event_handler import ShortcutEventHandler
from core.client.shortcut.task import ShortcutTask

if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.app import CapsWriterClient

if platform.system() == 'Darwin':
    import Quartz
else:
    Quartz = None



class ShortcutManager:
    """
    快捷键管理器

    统一管理多个快捷键，使用 pynput 监听键盘和鼠标事件。
    所有事件处理都在 win32_event_filter 中完成，确保高性能和低延迟。
    """

    def __init__(self, app: CapsWriterClient, shortcuts: List[Shortcut]):
        """
        初始化快捷键管理器

        Args:
            app: 客户端 App 实例
            shortcuts: 快捷键配置列表
        """
        self.app = app
        self.shortcuts = shortcuts
        self._system_name = platform.system()

        # 监听器
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None

        # 快捷键任务映射（key -> ShortcutTask）
        self.tasks: Dict[str, ShortcutTask] = {}

        # 线程池
        self._pool = ThreadPoolExecutor(max_workers=4)

        # 按键模拟器
        self._emulator = ShortcutEmulator()

        # 按键恢复状态追踪
        self._restoring_keys = set()

        # macOS 下普通按键会通过 `pynput` 的 `on_press` / `on_release` 回调进入。
        # 这里单独记录物理按下集合，避免自动重复触发时反复启动录音。
        self._pressed_keys = set()

        # 事件处理器
        self._event_handler = ShortcutEventHandler(self.tasks, self._pool, self._emulator)

        # 初始化快捷键任务
        self._init_tasks()

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _init_tasks(self) -> None:
        """初始化所有快捷键任务"""
        from config_client import ClientConfig as Config

        for shortcut in self.shortcuts:
            if not shortcut.enabled:
                continue

            task = ShortcutTask(self.app, shortcut)
            task._manager_ref = lambda: self  # 弱引用，用于回调
            task.pool = self._pool
            task.threshold = shortcut.get_threshold(Config.threshold)
            self.tasks[shortcut.key] = task

    @staticmethod
    def _key_to_name(key) -> Optional[str]:
        """
        将 `pynput` 的按键对象转为配置里使用的标准键名。

        macOS 普通按键事件会直接走 `pynput` 回调，这里负责把 `Key`/`KeyCode`
        统一映射回 `caps_lock`、`f12`、`a` 这类配置键名。
        """
        if key is None:
            return None

        if isinstance(key, keyboard.Key):
            return key.name

        if isinstance(key, keyboard.KeyCode):
            if key.char is not None:
                return key.char.lower()
            if key.vk is not None:
                return KeyMapper.vk_to_name(key.vk)

        return None

    # ========== 监听器创建 ==========

    def create_keyboard_filter(self):
        """创建键盘事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 KEYDOWN 和 KEYUP 消息
            if msg not in KEYBOARD_MESSAGES:
                return True

            key_name = KeyMapper.vk_to_name(data.vkCode)

            # 防自捕获检查
            if self._check_emulating(key_name, msg):
                return True
            if self._check_restoring(key_name, msg):
                return True

            # 查找匹配的快捷键
            if key_name not in self.tasks:
                return True

            task = self.tasks[key_name]

            # 处理按键事件
            if msg in KEY_DOWN_MESSAGES:
                self._event_handler.handle_keydown(key_name, task)
            elif msg in KEY_UP_MESSAGES:
                self._event_handler.handle_keyup(key_name, task)

            # 阻塞事件
            if task.shortcut.suppress and self.keyboard_listener:
                self.keyboard_listener.suppress_event()

            return True

        return win32_event_filter

    def create_mouse_filter(self):
        """创建鼠标事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 XBUTTON 消息
            if msg not in MOUSE_MESSAGES:
                return True

            # 获取按键标识
            xbutton = (data.mouseData >> 16) & 0xFFFF
            button_name = 'x1' if xbutton == XBUTTON1 else 'x2'

            # 防自捕获检查
            if self._check_emulating(button_name, msg, is_mouse=True):
                return True

            # 查找匹配的快捷键
            if button_name not in self.tasks:
                return True

            task = self.tasks[button_name]

            # 处理鼠标事件
            if msg == WM_XBUTTONDOWN:
                self._event_handler.handle_keydown(button_name, task)
            elif msg == WM_XBUTTONUP:
                self._handle_mouse_keyup(button_name, task)

            # 阻塞事件
            if task.shortcut.suppress and self.mouse_listener:
                self.mouse_listener.suppress_event()

            return True

        return win32_event_filter

    def create_darwin_mouse_interceptor(self):
        """
        创建 macOS 鼠标事件拦截器。

        当前只补齐 X1/X2 扩展按键的最小事件映射，保证现有配置在 macOS 下
        至少不会因为继续走 Win32 过滤器而完全失效。
        """
        if Quartz is None:
            return None

        def darwin_intercept(event_type, event):
            if event_type not in (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp):
                return event

            button_number = Quartz.CGEventGetIntegerValueField(
                event,
                Quartz.kCGMouseEventButtonNumber,
            )

            # macOS 中额外鼠标键在 CoreGraphics 里按 3/4/... 编号。
            # 这里按常见浏览器后退/前进键映射到项目内部的 x1/x2 命名。
            button_name_map = {
                3: 'x1',
                4: 'x2',
            }
            button_name = button_name_map.get(button_number)
            if button_name is None or button_name not in self.tasks:
                return event

            if self._check_emulating_mac(button_name, event_type, is_mouse=True):
                return None

            task = self.tasks[button_name]
            if event_type == Quartz.kCGEventOtherMouseDown:
                self._dispatch_task_keydown(button_name, task)
            else:
                self._handle_mouse_keyup(button_name, task)

            if task.shortcut.suppress:
                return None

            return event

        return darwin_intercept

    def _handle_mouse_keyup(self, button_name: str, task) -> None:
        """处理鼠标按键释放事件"""
        # 单击模式
        if not task.shortcut.hold_mode:
            if task.pressed:
                task.pressed = False
                task.released = True
                task.event.set()
            return

        # 长按模式
        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{button_name}] 松开按键，持续时间: {duration:.3f}s")

        if duration < task.threshold:
            task.cancel()
            if task.shortcut.suppress:
                logger.debug(f"[{button_name}] 安排异步补发鼠标按键")
                self._pool.submit(self._emulator.emulate_mouse_click, button_name)
        else:
            task.finish()

    def _dispatch_task_keydown(self, key_name: str, task) -> None:
        """
        将“某个逻辑键已按下”统一分发给事件处理器。

        Windows 走 Win32 消息过滤器，macOS 的 `Caps Lock` 和扩展鼠标键
        走这里，保证录音状态机仍复用同一套现有逻辑。
        """
        self._event_handler.handle_keydown(key_name, task)

    def _dispatch_task_keyup(self, key_name: str, task) -> None:
        """将“某个逻辑键已释放”统一分发给事件处理器。"""
        self._event_handler.handle_keyup(key_name, task)

    def start_press_to_talk(self, key_name: str) -> None:
        """
        按平台语义直接启动一次“按住说话”录音。

        这里不经过 `handle_keydown` 的原因是：
        - 新的 macOS `Caps Lock -> F18` 路线需要先经历“短按/长按”判定；
        - 一旦确认是长按，应该立即进入现有录音链路，而不是再走一遍
          `hold_mode` 的键盘事件状态机。
        """
        task = self.tasks.get(key_name)
        if task is None:
            logger.debug(f"[{key_name}] 未找到快捷键任务，忽略 start_press_to_talk")
            return

        if task.is_recording:
            logger.debug(f"[{key_name}] 录音任务已在运行，忽略重复 start_press_to_talk")
            return

        logger.info(f"[{key_name}] 语义长按成立，启动按住说话录音")
        task.launch()

    def stop_press_to_talk(self, key_name: str) -> None:
        """
        按平台语义结束一次“按住说话”录音。

        只有在任务已真正进入录音状态时才结束，避免短按路径误触发 finish。
        """
        task = self.tasks.get(key_name)
        if task is None:
            logger.debug(f"[{key_name}] 未找到快捷键任务，忽略 stop_press_to_talk")
            return

        if not task.is_recording:
            logger.debug(f"[{key_name}] 当前未在录音，忽略 stop_press_to_talk")
            return

        logger.info(f"[{key_name}] 语义长按结束，停止按住说话录音")
        task.finish()

    # ========== 按键恢复管理 ==========

    def schedule_restore(self, key: str) -> None:
        """
        安排按键恢复（延迟执行，避免在事件处理中阻塞）

        Args:
            key: 要恢复的按键

        注意：标志清除只在按键释放事件中处理（_check_restoring），
        避免在线程中提前清除导致主线程收到重复消息。
        """
        from pynput import keyboard

        self._restoring_keys.add(key)

        def do_restore():
            import time
            time.sleep(0.05)  # 延迟 50ms
            if key == 'caps_lock':
                controller = keyboard.Controller()
                controller.press(keyboard.Key.caps_lock)
                controller.release(keyboard.Key.caps_lock)

        self._pool.submit(do_restore)

    def is_restoring(self, key: str) -> bool:
        """检查是否正在恢复指定按键"""
        return key in self._restoring_keys

    def clear_restoring_flag(self, key: str) -> None:
        """清除恢复标志"""
        self._restoring_keys.discard(key)

    # ========== 防自捕获检查 ==========

    def _check_emulating(self, key_name: str, msg: int, is_mouse: bool = False) -> bool:
        """检查是否正在模拟按键"""
        if not self._emulator.is_emulating(key_name):
            return False

        # 松开时清除标志
        if is_mouse:
            if msg == WM_XBUTTONUP:
                self._emulator.clear_emulating_flag(key_name)
        else:
            if msg in (WM_KEYUP, WM_SYSKEYUP):
                self._emulator.clear_emulating_flag(key_name)

        return True  # 放行

    def _check_emulating_mac(
        self,
        key_name: str,
        event_type,
        is_mouse: bool = False,
        is_key_down: Optional[bool] = None,
    ) -> bool:
        """
        macOS 版防自捕获检查。

        这里不依赖 Win32 消息常量，而是只关注“当前事件是否来自我们刚刚补发的按键”。
        在释放阶段清理标志，避免后续真实按键被持续误判为模拟事件。
        """
        if not self._emulator.is_emulating(key_name):
            return False

        if is_mouse:
            if Quartz is not None and event_type == Quartz.kCGEventOtherMouseUp:
                self._emulator.clear_emulating_flag(key_name)
        else:
            if is_key_down is False:
                self._emulator.clear_emulating_flag(key_name)

        return True

    def _check_restoring(self, key_name: str, msg: int) -> bool:
        """检查是否正在恢复按键"""
        if not self.is_restoring(key_name):
            return False

        if msg in (WM_KEYUP, WM_SYSKEYUP):
            self.clear_restoring_flag(key_name)

        return True  # 放行

    def _check_restoring_mac(self, key_name: str, event_type, is_key_down: Optional[bool] = None) -> bool:
        """macOS 版按键恢复防自捕获检查。"""
        if not self.is_restoring(key_name):
            return False

        if is_key_down is False:
            self.clear_restoring_flag(key_name)

        return True

    def _on_darwin_press(self, key) -> None:
        """
        macOS 普通键盘按下回调。

        `Caps Lock` 由底层拦截器单独处理，这里只负责其它普通键，
        并用 `_pressed_keys` 去掉长按自动重复导致的重复触发。
        """
        key_name = self._key_to_name(key)
        if not key_name or key_name == 'caps_lock':
            return

        if key_name in self._pressed_keys:
            return
        self._pressed_keys.add(key_name)

        if key_name not in self.tasks:
            return

        self._dispatch_task_keydown(key_name, self.tasks[key_name])

    def _on_darwin_release(self, key) -> None:
        """macOS 普通键盘释放回调。"""
        key_name = self._key_to_name(key)
        if not key_name or key_name == 'caps_lock':
            return

        self._pressed_keys.discard(key_name)

        if key_name not in self.tasks:
            return

        self._dispatch_task_keyup(key_name, self.tasks[key_name])

    # ========== 公共接口 ==========

    def start(self) -> None:
        """启动所有监听器"""
        has_keyboard = any(s.type == 'keyboard' for s in self.shortcuts if s.enabled)
        has_mouse = any(s.type == 'mouse' for s in self.shortcuts if s.enabled)

        if has_keyboard:
            if self.keyboard_listener and self.keyboard_listener.is_alive():
                logger.debug("键盘监听器已在运行，跳过启动")
            else:
                if self._system_name == 'Darwin':
                    self.keyboard_listener = keyboard.Listener(
                        on_press=self._on_darwin_press,
                        on_release=self._on_darwin_release,
                    )
                else:
                    self.keyboard_listener = keyboard.Listener(
                        win32_event_filter=self.create_keyboard_filter()
                    )
                self.keyboard_listener.start()
                logger.info("键盘监听器已启动")

        if has_mouse:
            if self.mouse_listener and self.mouse_listener.is_alive():
                logger.debug("鼠标监听器已在运行，跳过启动")
            else:
                if self._system_name == 'Darwin':
                    self.mouse_listener = mouse.Listener(
                        darwin_intercept=self.create_darwin_mouse_interceptor()
                    )
                else:
                    self.mouse_listener = mouse.Listener(
                        win32_event_filter=self.create_mouse_filter()
                    )
                self.mouse_listener.start()
                logger.info("鼠标监听器已启动")

        # 打印所有启用的快捷键
        for shortcut in self.shortcuts:
            if shortcut.enabled:
                mode = "长按" if shortcut.hold_mode else "单击"
                toggle = "可恢复" if shortcut.is_toggle_key() else "普通键"
                logger.info(f"  [{shortcut.key}] {mode}模式, 阻塞:{shortcut.suppress}, {toggle}")

    def stop(self) -> None:
        """停止所有监听器和清理资源"""
        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
                logger.debug("键盘监听器已停止")
            except Exception:
                pass
            finally:
                self.keyboard_listener = None
                
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
                logger.debug("鼠标监听器已停止")
            except Exception:
                pass
            finally:
                self.mouse_listener = None

        # 取消所有任务
        for task in self.tasks.values():
            if task.is_recording:
                task.cancel()

        # 关闭线程池
        self._pool.shutdown(wait=False)
        logger.debug("快捷键管理器线程池已关闭")

    def should_restore_key_after_finish(self, key: str, shortcut) -> bool:
        """
        判断录音完成后是否需要主动 restore 某个切换键。

        设计原则：
        - 非切换键永远不需要 restore。
        - 常规历史语义仍保持兼容：非阻塞模式下需要 restore；
          旧版 Darwin 兜底逻辑也仍保留。
        - 当 macOS 已改走 `Caps Lock -> F18` 路线时，长按录音不会直接改变
          系统大小写锁定状态，因此结束录音后不能再补一次 `Caps Lock`。
        """
        if not shortcut.is_toggle_key():
            return False

        if self._system_name == 'Darwin' and key == 'caps_lock':
            from config_client import ClientConfig as Config

            if getattr(Config, 'macos_caps_mode', 'off') == 'remap_f18':
                return False

        return (not shortcut.suppress) or self._system_name == 'Darwin'
