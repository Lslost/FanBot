"""发现机器人里已经安装的插件，并整理成可展示的信息。

插件来源分三类：

* `local`  ：`src/plugins` 等本地插件目录下的插件
* `pip`    ：通过 pip 安装的第三方插件（`nonebot-plugin-*`）
* `builtin`：NoneBot 自带的内置插件（`nonebot.plugins.*`）

对于还没有被加载的本地插件与第三方插件，也会一并列出，方便在聊天里查看
（是否加载由启动配置决定，这里只做展示）。
"""

from __future__ import annotations

import pkgutil
import time
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import nonebot
from nonebot.log import logger

from .config import get_config

if TYPE_CHECKING:
    from nonebot.plugin import Plugin

__all__ = ["PluginInfo", "collect_plugins", "find_plugins"]

SOURCE_LABELS = {"local": "本地", "pip": "第三方", "builtin": "内置"}
SOURCE_ORDER = {"local": 0, "pip": 1, "builtin": 2}

PIP_SCAN_TTL = 60.0
_pip_cache: tuple[float, tuple[tuple[str, str, str | None, str, str | None], ...]] | None = (
    None
)


@dataclass(slots=True)
class PluginInfo:
    """一个插件的展示信息。"""

    id: str
    """插件标识符"""
    name: str
    """插件显示名称"""
    module_name: str | None = None
    """点分割的模块路径"""
    source: str = "local"
    """来源：local / pip / builtin"""
    version: str | None = None
    description: str = ""
    usage: str = ""
    homepage: str | None = None
    category: str | None = None
    loaded: bool = False
    """是否已被 NoneBot 导入"""
    matcher_count: int = 0
    """加载时注册的事件响应器数量"""

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    @property
    def status_label(self) -> str:
        return "启用" if self.loaded else "未加载"


def collect_plugins() -> list[PluginInfo]:
    """汇总当前机器人里已安装的全部插件。"""

    config = get_config()
    infos: dict[str, PluginInfo] = {}
    known_modules: set[str] = set()

    for plugin in sorted(nonebot.get_loaded_plugins(), key=lambda item: item.id_):
        info = _info_from_loaded(plugin, config.plugin_dirs)
        if info.source == "builtin" and not config.show_builtin:
            continue
        infos[info.id] = info
        known_modules.add(plugin.module_name)

    for plugin_id, module_name in _iter_local_plugins(config.plugin_dirs):
        if plugin_id in infos or module_name in known_modules:
            continue
        infos[plugin_id] = PluginInfo(
            id=plugin_id, name=plugin_id, module_name=module_name, source="local"
        )
        known_modules.add(module_name)

    for plugin_id, module_name, version, description, homepage in _iter_pip_plugins():
        if plugin_id in infos or module_name in known_modules:
            continue
        infos[plugin_id] = PluginInfo(
            id=plugin_id,
            name=plugin_id,
            module_name=module_name,
            source="pip",
            version=version,
            description=description,
            homepage=homepage,
        )
        known_modules.add(module_name)

    plugins = list(infos.values())
    plugins.sort(key=lambda item: (SOURCE_ORDER.get(item.source, 9), item.id.lower()))
    return plugins


def find_plugins(plugins: Iterable[PluginInfo], keyword: str) -> list[PluginInfo]:
    """按标识符 / 名称查找插件，先精确匹配再模糊匹配。"""

    plugins = list(plugins)
    key = keyword.strip().lower()
    if not key:
        return []

    exact = [
        plugin
        for plugin in plugins
        if plugin.id.lower() == key or plugin.name.lower() == key
    ]
    if exact:
        return exact

    return [
        plugin
        for plugin in plugins
        if key in plugin.id.lower() or key in plugin.name.lower()
    ]


def _info_from_loaded(plugin: Plugin, plugin_dirs: Iterable[str]) -> PluginInfo:
    metadata_info = plugin.metadata
    version: str | None = None
    description = ""
    usage = ""
    homepage: str | None = None
    category: str | None = None

    if metadata_info is not None:
        description = metadata_info.description or ""
        usage = metadata_info.usage or ""
        homepage = metadata_info.homepage or None
        category = metadata_info.type or None
        extra = metadata_info.extra or {}
        raw_version = extra.get("version") if isinstance(extra, dict) else None
        if isinstance(raw_version, str):
            version = raw_version

    if version is None:
        version = _distribution_version(plugin.module_name)

    return PluginInfo(
        id=plugin.id_,
        name=plugin.name,
        module_name=plugin.module_name,
        source=_classify_source(plugin, plugin_dirs),
        version=version,
        description=description,
        usage=usage,
        homepage=homepage,
        category=category,
        loaded=True,
        matcher_count=len(plugin.matcher),
    )


def _classify_source(plugin: Plugin, plugin_dirs: Iterable[str]) -> str:
    if plugin.module_name.startswith("nonebot.plugins"):
        return "builtin"

    module_file = getattr(plugin.module, "__file__", None)
    if module_file:
        path = Path(module_file).resolve()
        for directory in plugin_dirs:
            try:
                path.relative_to(Path(directory).expanduser().resolve())
            except (OSError, ValueError):
                continue
            return "local"

    return "pip"


def _distribution_version(module_name: str) -> str | None:
    """尝试通过模块名反查 pip 包版本。"""

    top_module = module_name.split(".")[0]
    for dist_name in (top_module, top_module.replace("_", "-")):
        try:
            return metadata.version(dist_name)
        except metadata.PackageNotFoundError:
            continue
    return None


def _iter_local_plugins(plugin_dirs: Iterable[str]) -> list[tuple[str, str]]:
    """扫描本地插件目录，返回 (插件标识符, 模块路径)。"""

    found: dict[str, str] = {}
    for directory in plugin_dirs:
        path = Path(directory).expanduser()
        if not path.is_dir():
            continue
        for module_info in pkgutil.iter_modules([str(path)]):
            if module_info.name.startswith("_"):
                continue
            module_name = _module_name_of(path, module_info.name)
            found.setdefault(module_info.name, module_name)
    return sorted(found.items())


def _module_name_of(directory: Path, name: str) -> str:
    """把 `src/plugins/foo` 转成 `src.plugins.foo`。"""

    try:
        relative = directory.resolve().relative_to(Path.cwd().resolve())
    except (OSError, ValueError):
        return name
    return ".".join((*relative.parts, name))


def _iter_pip_plugins() -> tuple[tuple[str, str, str | None, str, str | None], ...]:
    """扫描已安装的第三方插件（带缓存，避免频繁扫描）。"""

    global _pip_cache

    now = time.monotonic()
    if _pip_cache is None or now - _pip_cache[0] > PIP_SCAN_TTL:
        try:
            entries = tuple(_scan_pip_plugins())
        except Exception as error:  # pragma: no cover - 环境异常时不影响其他功能
            logger.opt(exception=error).warning("plugin_manager: 扫描第三方插件失败")
            entries = ()
        _pip_cache = (now, entries)
    return _pip_cache[1]


def _scan_pip_plugins() -> list[tuple[str, str, str | None, str, str | None]]:
    entries: list[tuple[str, str, str | None, str, str | None]] = []
    for dist in metadata.distributions():
        try:
            raw_name = dist.metadata["Name"]
        except Exception:
            continue
        if not raw_name:
            continue
        if not raw_name.replace("_", "-").lower().startswith("nonebot-plugin"):
            continue

        module_name = _top_level_module(dist, raw_name)
        if not module_name:
            continue

        summary = dist.metadata["Summary"] or ""
        homepage = _homepage_of(dist)
        entries.append(
            (
                module_name.rsplit(".", 1)[-1],
                module_name,
                dist.version,
                summary.strip(),
                homepage,
            )
        )
    return entries


def _homepage_of(dist: metadata.Distribution) -> str | None:
    """获取插件主页地址。"""

    home_page = (dist.metadata["Home-page"] or "").strip()
    if home_page:
        return home_page

    project_url = (dist.metadata["Project-URL"] or "").strip()
    if not project_url:
        return None
    # Project-URL 的格式为「标签, 地址」，可能有多行，取第一行即可
    return project_url.splitlines()[0].split(",", 1)[-1].strip() or None


def _top_level_module(dist: metadata.Distribution, dist_name: str) -> str | None:
    try:
        top_level = dist.read_text("top_level.txt")
    except Exception:
        top_level = None

    if top_level:
        for line in top_level.splitlines():
            name = line.strip()
            if name and not name.startswith("_"):
                return name

    guessed = dist_name.replace("-", "_")
    return guessed if guessed.startswith("nonebot_plugin") else None
