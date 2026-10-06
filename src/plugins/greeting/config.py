"""早安 / 晚安插件的配置项。

所有配置项都在 `.env` 的 `GREETING__` 命名空间下，例如：

    GREETING__DAY_BOUNDARY_HOUR=4
    GREETING__DB_PATH=/home/lighthouse/break/data/greeting.db
"""

from __future__ import annotations

from functools import lru_cache

from nonebot import get_plugin_config
from nonebot.log import logger
from pydantic import BaseModel, Field


class GreetingConfig(BaseModel):
    """`greeting` 插件的属性配置。"""

    db_path: str | None = None
    """SQLite 数据库路径，默认为 `data/greeting/greeting.db`（安装了 localstore 时使用其数据目录）"""

    day_boundary_hour: int = Field(default=4, ge=0, le=12)
    """几点算作新的一天，默认 4 点。

    因为常常有人凌晨才睡，把凌晨到 4 点之间的「早安 / 晚安」算在前一天，
    打卡排名与「今天」的判定都以此为准。
    """

    morning_start: str = "04:00"
    """早安卡开始时间（HH:MM，含），留空表示不限时"""

    morning_end: str = "11:00"
    """早安卡结束时间（HH:MM，含），留空表示不限时"""

    night_start: str = "21:00"
    """晚安卡开始时间（HH:MM，含），留空表示不限时"""

    night_end: str = "06:00"
    """晚安卡结束时间（HH:MM，含），留空表示不限时。

    结束时间早于开始时间表示跨零点，例如默认的 21:00–06:00。
    """


class Config(BaseModel):
    greeting: GreetingConfig = Field(default_factory=GreetingConfig)


@lru_cache(maxsize=1)
def get_config() -> GreetingConfig:
    """获取插件配置，NoneBot 未初始化或配置有误时回退到默认值。"""

    try:
        return get_plugin_config(Config).greeting
    except ValueError as error:
        logger.debug(f"greeting: 读取配置失败，使用默认配置 ({error})")
        return GreetingConfig()
