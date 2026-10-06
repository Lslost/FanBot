"""把渲染好的图片发送出去，适配器不支持图片时自动回退为文本。"""

from __future__ import annotations

from nonebot.adapters import Event, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher

__all__ = ["build_image_segment", "finish_with_image"]


def build_image_segment(event: Event, image: bytes) -> MessageSegment | None:
    """按当前适配器构造图片消息段，当前适配器不支持图片时返回 `None`。"""

    try:
        segment_class = type(event.get_message()).get_segment_class()
    except Exception as error:  # pragma: no cover - 极端异常时才触发
        logger.opt(exception=error).debug("plugin_manager: 获取消息段类型失败")
        return None

    factory = getattr(segment_class, "image", None)
    if factory is None:
        return None

    try:
        return factory(image)
    except Exception as error:
        logger.opt(exception=error).warning(
            "plugin_manager: 构造图片消息失败，本次改用文本发送"
        )
        return None


async def finish_with_image(
    matcher: Matcher, event: Event, image: bytes, fallback: str
) -> None:
    """优先结束会话并发送图片，失败时发送文本。"""

    if (segment := build_image_segment(event, image)) is not None:
        await matcher.finish(segment)
    await matcher.finish(fallback)
