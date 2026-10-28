"""早安 / 晚安打卡：记录打卡时间与排名，并汇报睡眠时长。"""

from __future__ import annotations

from datetime import datetime, timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata

from .config import get_config
from .database import get_database
from .utils import (
    build_at_segment,
    day_key,
    detect_gender,
    detect_nickname,
    detect_scope_id,
    fetch_group_gender,
    format_duration,
    gender_label,
    parse_time_window,
)

__plugin_meta__ = PluginMetadata(
    name="早安晚安",
    description="记录群友的早安 / 晚安打卡，汇报打卡排名与睡眠时长（数据存在本地 SQLite）",
    usage=(
        "早安 / 早上好   打早安卡（默认只在 04:00–11:00 有效），"
        "返回今天第几个早安的群友 / 少女 / 少年，并汇报睡眠时长\n"
        "晚安 / 好梦     打晚安卡（默认只在 21:00–06:00 有效），"
        "返回今天第几个晚安的群友 / 少女 / 少年"
    ),
    type="娱乐",
    supported_adapters=None,
)

morning_matcher = on_command(
    "早安", aliases={"早上好", "morning", "おはよう"}, priority=5, block=True
)
night_matcher = on_command("晚安", aliases={"好梦", "goodnight", "おやすみ", "oysm"}, priority=5, block=True)

MAX_SLEEP = timedelta(hours=20)
"""超过这个时长就不当成「睡了一觉」，只提示作息"""


def _with_mention(event: Event, text: str) -> str | Message:
    """能 @ 到人的时候，在提示语前面加上 @。"""

    mention = build_at_segment(event)
    return mention + " " + text if mention is not None else text


def _greeting_line(
    event: Event,
    *,
    emoji: str,
    greeting: str,
    nickname: str | None,
    rank: int,
    label: str,
) -> str | Message:
    """回复的第一行：群里 @ 发送者，取不到 @ 时退回昵称。"""

    suffix = f"！你是今天第 {rank} 个{greeting}的{label}。"

    if (mention := build_at_segment(event)) is not None:
        # 已经 @ 到本人，正文里就不用再重复名字了
        return mention + f" {emoji} {greeting}{suffix}"

    name = f"，{nickname}" if nickname else ""
    return f"{emoji} {greeting}{name}{suffix}"


def _sleep_line(now: datetime, last_night: datetime | None) -> str:
    """根据上一次晚安时间拼出睡眠时长文案。"""

    if last_night is None:
        return "😴 还没有你的晚安记录，今晚记得早点休息～"

    duration = now - last_night
    if duration > MAX_SLEEP:
        return f"😴 距上次晚安已经 {format_duration(duration)}，作息要规律一点哦。"
    return (
        f"😴 从 {last_night:%H:%M} 睡到 {now:%H:%M}，"
        f"一共睡了 {format_duration(duration)}。"
    )


def _out_of_window(event: Event, greeting: str, window_label: str) -> Message:
    """不在打卡时段内时的提示。"""

    return _with_mention(
        event, f"⏰ 现在不是{greeting}时间哦，{greeting}只在 {window_label} 之间有效～"
    )


@morning_matcher.handle()
async def _handle_morning(bot: Bot, event: Event, matcher: Matcher) -> None:
    now = datetime.now()
    config = get_config()
    database = get_database()

    scope_id = detect_scope_id(event)
    user_id = event.get_user_id()
    nickname = detect_nickname(event)
    day = day_key(now, config.day_boundary_hour)

    window = parse_time_window(config.morning_start, config.morning_end)
    if window is not None and not window.contains(now.time()):
        await matcher.finish(_out_of_window(event, "早安", window.label))

    gender = (
        detect_gender(event)
        or database.gender_of(user_id)
        or await fetch_group_gender(bot, event)
        or "unknown"
    )
    check_in = database.check_in(
        scope_id=scope_id,
        user_id=user_id,
        kind="morning",
        day=day,
        nickname=nickname,
        gender=gender,
        now=now,
    )
    label = gender_label(gender)

    if check_in.already:
        await matcher.finish(
            _with_mention(
                event, f"你今天已经早安啦，是今天第 {check_in.rank} 个早安的{label}。"
            )
        )

    reply = _greeting_line(
        event,
        emoji="🌅",
        greeting="早安",
        nickname=nickname,
        rank=check_in.rank,
        label=label,
    )
    reply = reply + "\n" + _sleep_line(now, database.last_time(user_id, "night"))
    await matcher.finish(reply)


@night_matcher.handle()
async def _handle_night(bot: Bot, event: Event, matcher: Matcher) -> None:
    now = datetime.now()
    config = get_config()
    database = get_database()

    scope_id = detect_scope_id(event)
    user_id = event.get_user_id()
    nickname = detect_nickname(event)
    day = day_key(now, config.day_boundary_hour)

    window = parse_time_window(config.night_start, config.night_end)
    if window is not None and not window.contains(now.time()):
        await matcher.finish(_out_of_window(event, "晚安", window.label))

    gender = (
        detect_gender(event)
        or database.gender_of(user_id)
        or await fetch_group_gender(bot, event)
        or "unknown"
    )
    check_in = database.check_in(
        scope_id=scope_id,
        user_id=user_id,
        kind="night",
        day=day,
        nickname=nickname,
        gender=gender,
        now=now,
    )
    label = gender_label(gender)

    if check_in.already:
        await matcher.finish(
            _with_mention(
                event, f"你今天已经晚安啦，是今天第 {check_in.rank} 个晚安的{label}。"
            )
        )

    reply = _greeting_line(
        event,
        emoji="🌙",
        greeting="晚安",
        nickname=nickname,
        rank=check_in.rank,
        label=label,
    )

    morning_time = database.time_of_day(
        scope_id=scope_id, user_id=user_id, kind="morning", day=day
    )
    if morning_time is not None:
        reply = reply + f"\n🌅 今天你 {morning_time:%H:%M} 就起床了，快好好休息吧～"

    await matcher.finish(reply)
