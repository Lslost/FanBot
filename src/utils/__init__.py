"""机器人通用工具：退出进程、安装 / 卸载插件。"""

from .bot_manage import (
    PROJECT_ROOT,
    is_valid_plugin_name,
    plugin_install,
    plugin_uninstall,
    shutdown,
)

__all__ = [
    "PROJECT_ROOT",
    "is_valid_plugin_name",
    "plugin_install",
    "plugin_uninstall",
    "shutdown",
]
