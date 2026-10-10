"""插件管理插件的配置项。

所有配置项都放在 `.env` 的 `PLUGIN_MANAGER__` 命名空间下，例如：

    PLUGIN_MANAGER__PAGESIZE=12
    PLUGIN_MANAGER__FONT_PATH=/usr/share/fonts/xxx.ttf
    PLUGIN_MANAGER__PLUGIN_DIRS=["src/plugins"]
"""

from __future__ import annotations

from functools import lru_cache

from nonebot import get_plugin_config
from nonebot.log import logger
from pydantic import BaseModel, Field


class PluginManagerConfig(BaseModel):
    """`plugin_manager` 的属性配置。"""

    pagesize: int = Field(default=9, ge=1, le=30)
    """插件列表图片每页显示的插件数量"""

    plugin_dirs: list[str] = Field(default_factory=lambda: ["src/plugins"])
    """需要扫描的本地插件目录（相对当前工作目录）"""

    font_path: str | None = None
    """自定义正文字体（需要支持中文）。默认依次查找 Noto Sans CJK SC / 思源黑体 / 文泉驿"""

    font_bold_path: str | None = None
    """自定义标题字体（需要支持中文）。默认同上，和正文字体同族"""

    show_builtin: bool = True
    """是否在插件列表中显示 NoneBot 内置插件"""


class Config(BaseModel):
    plugin_manager: PluginManagerConfig = Field(default_factory=PluginManagerConfig)


@lru_cache(maxsize=1)
def get_config() -> PluginManagerConfig:
    """获取插件配置。

    NoneBot 尚未初始化（例如单元测试）或配置项非法时，回退到默认配置，
    避免整个插件因为配置问题而无法加载。
    """

    try:
        return get_plugin_config(Config).plugin_manager
    except ValueError as error:
        logger.debug(f"plugin_manager: 读取配置失败，使用默认配置 ({error})")
        return PluginManagerConfig()
