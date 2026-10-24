"""性别识别、昵称提取、会话范围与时间格式化等小工具。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING

from nonebot.log import logger

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Event, MessageSegment

__all__ = [
    "GENDER_LABELS",
    "TimeWindow",
    "build_at_segment",
    "day_key",
    "detect_gender",
    "fetch_group_gender",
    "detect_nickname",
    "detect_scope_id",
    "format_duration",
    "gender_label",
    "is_group_scope",
    "parse_time_window",
]

GENDER_LABELS = {"female": "少女", "male": "少年", "unknown": "群友"}
"""性别对应的称呼，取不到性别时叫「群友」"""

MALE_VALUES = {"male", "m", "boy", "男", "男性", "1"}
FEMALE_VALUES = {"female", "f", "girl", "女", "女性", "2"}


def gender_label(gender: str | None) -> str:
    """把性别转换成称呼。"""

    return GENDER_LABELS.get(gender or "unknown", "群友")


def normalize_gender(value: object) -> str | None:
    """把各种写法的性别统一成 male / female，未知返回 None。"""

    if not isinstance(value, str):
        return None

    key = value.strip().lower()
    if key in MALE_VALUES:
        return "male"
    if key in FEMALE_VALUES:
        return "female"
    return None


def _raw_gender(holder: object) -> str | None:
    if holder is None:
        return None

    for attr in ("sex", "gender"):
        value = getattr(holder, attr, None)
        if isinstance(value, str) and value.strip():
            return value

    if isinstance(holder, Mapping):
        for key in ("sex", "gender"):
            value = holder.get(key)
            if isinstance(value, str) and value.strip():
                return value

    return None


def detect_gender(event: Event) -> str | None:
    """从事件里猜测性别。

    不同适配器放性别的位置不一样（例如 OneBot v11 是 `event.sender.sex`，
    console 适配器则没有这个信息），因此这里逐层尝试，猜不到就返回 None。
    """

    for holder in (
        getattr(event, "sender", None),
        getattr(event, "user", None),
        event,
    ):
        gender = normalize_gender(_raw_gender(holder))
        if gender:
            return gender
    return None


async def fetch_group_gender(bot: Bot, event: Event) -> str | None:
    """从群成员资料里补查性别。

    部分协议端的消息事件没有填写 ``sender.sex``，但群成员资料接口通常仍有
    该字段。只处理群消息；接口不可用或没有返回性别时返回 ``None``。
    """

    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None

    user_id = getattr(event, "user_id", None)
    if user_id is None:
        user_id = event.get_user_id()

    try:
        member = await bot.call_api(
            "get_group_member_info",
            group_id=group_id,
            user_id=user_id,
            no_cache=False,
        )
    except Exception as error:
        logger.opt(exception=error).debug(
            f"greeting: 查询群成员 {user_id} 的性别失败"
        )
        return None

    return normalize_gender(_raw_gender(member))


def detect_nickname(event: Event) -> str | None:
    """从事件里取出昵称（优先群名片）。"""

    for holder in (getattr(event, "sender", None), getattr(event, "user", None)):
        for attr in ("card", "nickname", "name"):
            value = getattr(holder, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def detect_scope_id(event: Event) -> str:
    """打卡计数的范围：群聊按群算，私聊按用户自己算。"""

    for attr in ("group_id", "channel_id", "guild_id"):
        value = getattr(event, attr, None)
        if value is not None:
            return f"group_{value}"
    return f"private_{event.get_user_id()}"


def is_group_scope(event: Event) -> bool:
    """事件是否来自群聊 / 频道。"""

    return any(
        getattr(event, attr, None) is not None
        for attr in ("group_id", "channel_id", "guild_id")
    )


def build_at_segment(event: Event) -> MessageSegment | None:
    """构造 @发送者 的消息段。

    私聊里 @ 没有意义，适配器不支持 @ 时也一样，这两种情况都返回 None，
    调用方可以退回使用昵称（与 OneBot 的 `at_sender=True` 只在群聊生效一致）。
    """

    if not is_group_scope(event):
        return None

    try:
        segment_class = type(event.get_message()).get_segment_class()
    except Exception as error:  # pragma: no cover - 极端异常时才触发
        logger.opt(exception=error).debug("greeting: 获取消息段类型失败")
        return None

    factory = getattr(segment_class, "at", None)
    if factory is None:
        return None

    try:
        return factory(event.get_user_id())
    except Exception as error:
        logger.opt(exception=error).warning("greeting: 构造 @ 消息段失败，改用昵称")
        return None


def day_key(now: datetime, boundary_hour: int) -> str:
    """以 `boundary_hour` 为分界把时间归到某一天。"""

    return (now - timedelta(hours=boundary_hour)).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """打卡时间窗，支持跨零点（例如 21:00–06:00）。"""

    start: dt_time
    end: dt_time

    def contains(self, moment: dt_time) -> bool:
        if self.start <= self.end:
            return self.start <= moment <= self.end
        return moment >= self.start or moment <= self.end

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


def _parse_time(text: str | None) -> dt_time | None:
    if not text or not text.strip():
        return None

    try:
        hour, minute = text.strip().split(":", 1)
        return dt_time(int(hour), int(minute))
    except (TypeError, ValueError):
        logger.warning(f"greeting: 时间「{text}」格式不正确，应为 HH:MM，该限制已忽略")
        return None


def parse_time_window(start: str | None, end: str | None) -> TimeWindow | None:
    """解析打卡时间窗；留空、格式错误或起止相同时表示不限时。"""

    start_time = _parse_time(start)
    end_time = _parse_time(end)
    if start_time is None or end_time is None or start_time == end_time:
        return None
    return TimeWindow(start_time, end_time)


def format_duration(duration: timedelta) -> str:
    """把时长格式化成「7 小时 32 分钟」。"""

    total_minutes = int(duration.total_seconds() // 60)
    if total_minutes <= 0:
        return "不到 1 分钟"

    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"
