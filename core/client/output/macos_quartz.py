# coding: utf-8
"""
macOS Quartz text injection.

This backend posts Unicode keyboard events to the currently focused control via
CGEventKeyboardSetUnicodeString. It avoids using the system clipboard.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import logger


class QuartzTextInjectionError(RuntimeError):
    """Raised when Quartz text injection cannot be performed."""


@dataclass(frozen=True)
class QuartzOutputOptions:
    chunk_size: int = 8
    key_delay: float = 0.002


class QuartzTextInjector:
    """Inject Unicode text with Quartz keyboard events."""

    def __init__(self, options: QuartzOutputOptions | None = None):
        self.options = options or QuartzOutputOptions()
        try:
            import Quartz
        except Exception as exc:
            raise QuartzTextInjectionError("Unable to import Quartz") from exc
        self._quartz = Quartz

    def type_text(self, text: str) -> None:
        """Inject text into the currently focused input location."""
        if not text:
            return

        chunk_size = max(1, int(self.options.chunk_size or 1))
        delay = max(0.0, float(self.options.key_delay or 0.0))

        logger.debug(
            "使用 Quartz Unicode 注入文本，长度: %s, chunk_size: %s",
            len(text),
            chunk_size,
        )

        for chunk in self._chunks(text, chunk_size):
            self._post_unicode_chunk(chunk)
            if delay:
                time.sleep(delay)

    @staticmethod
    def _chunks(text: str, chunk_size: int):
        for start in range(0, len(text), chunk_size):
            yield text[start:start + chunk_size]

    def _post_unicode_chunk(self, chunk: str) -> None:
        q = self._quartz

        key_down = q.CGEventCreateKeyboardEvent(None, 0, True)
        key_up = q.CGEventCreateKeyboardEvent(None, 0, False)
        if key_down is None or key_up is None:
            raise QuartzTextInjectionError("Unable to create Quartz keyboard event")

        for event in (key_down, key_up):
            q.CGEventSetFlags(event, 0)
            q.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)

        q.CGEventPost(q.kCGHIDEventTap, key_down)
        q.CGEventPost(q.kCGHIDEventTap, key_up)
