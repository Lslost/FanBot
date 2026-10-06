"""插件管理：列出机器人已安装的插件（图片）、查看详情，以及安装 / 卸载插件。"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .registry import PluginInfo, collect_plugins, find_plugins
from .render import (
    render_plugin_detail,
    render_plugin_detail_text,
    render_plugin_list,
    render_plugin_text,
)
from .sender import finish_with_image

try:  # 正常加载时插件模块是 `src.plugins.plugin_manager`，这里能直接拿到 utils
    from src.utils.bot_manage import (
        is_valid_plugin_name,
        plugin_install,
        plugin_uninstall,
    )
except ImportError:  # pragma: no cover - 插件目录不在 `src` 下时的兜底
    try:
        from utils.bot_manage import (  # type: ignore[no-redef]
            is_valid_plugin_name,
            plugin_install,
            plugin_uninstall,
        )
    except ImportError:
        is_valid_plugin_name = None  # type: ignore[assignment]
        plugin_install = plugin_uninstall = None  # type: ignore[assignment]

__plugin_meta__ = PluginMetadata(
    name="插件管理",
    description="以图片形式列出机器人已安装的全部插件，查看单个插件详情，并支持在线安装 / 卸载插件",
    usage=(
        "插件管理\n"
        "  插件列表 [页码|文本]   查看已安装的插件（图片）\n"
        "  插件信息 [名称]        查看单个插件的详情（图片）\n"
        "  插件安装 <包名>        安装插件（仅超级用户）\n"
        "  插件卸载 <包名>        卸载插件（仅超级用户）\n"
        "  插件帮助               查看本帮助"
    ),
    type="管理",
    supported_adapters=None,
)

HELP_TEXT = """📦 插件管理
/插件列表 [页码|文本]  列出机器人已安装的全部插件（图片）
/插件信息 [名称]  查看某个插件的详细信息，不带名称时等同于 /插件列表（图片）
/插件安装 <包名>  在线安装插件，例如 /插件安装 nonebot-plugin-foo（仅超级用户）
/插件卸载 <包名>  卸载已安装的插件，例如 /插件卸载 nonebot-plugin-foo（仅超级用户）
/插件帮助  显示这条帮助

说明：列表里会同时列出已加载的插件、本地插件目录下尚未加载的插件，
以及 pip 安装的第三方插件，插件是否加载由启动配置决定。
安装 / 卸载通过 nb-cli（`nb plugin install|uninstall`）完成，成功后会自动更新
plugin_index.json，重启机器人后生效；安装 / 卸载比较慢，请耐心等待。"""

list_matcher = on_command(
    "插件列表", aliases={"插件管理", "plugin_list", "plist"}, priority=5, block=True
)
info_matcher = on_command("插件信息", aliases={"plugin_info"}, priority=5, block=True)
help_matcher = on_command("插件帮助", aliases={"plugin_help"}, priority=5, block=True)
install_matcher = on_command(
    "插件安装",
    aliases={"安装插件", "插件下载", "plugin_install"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
uninstall_matcher = on_command(
    "插件卸载",
    aliases={"卸载插件", "插件删除", "plugin_uninstall"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


def _parse_page(raw: str) -> int | None:
    """解析页码参数，空字符串视为第一页。"""

    if not raw:
        return 1
    if raw.isdigit():
        return max(1, int(raw))
    return None


async def _resolve_plugin(
    matcher: Matcher, keyword: str, plugins: list[PluginInfo]
) -> PluginInfo | None:
    """把用户输入解析成唯一的插件，失败时直接结束会话。"""

    if not keyword:
        await matcher.finish("请提供插件名称，例如：/插件信息 plugin_manager")

    matches = find_plugins(plugins, keyword)
    if not matches:
        await matcher.finish(f"没有找到匹配「{keyword}」的插件，可发送 /插件列表 查看全部插件。")
    if len(matches) > 1:
        names = "、".join(match.id for match in matches[:8])
        await matcher.finish(f"「{keyword}」匹配到多个插件：{names}\n请使用更完整的名称。")

    return matches[0]


@list_matcher.handle()
async def _handle_list(event: Event, matcher: Matcher, args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip()
    plugins = collect_plugins()

    if raw.lower() in {"文本", "text", "txt", "-t", "--text"}:
        await matcher.finish(render_plugin_text(plugins))

    page = _parse_page(raw)
    if page is None:
        await matcher.finish(f"页码「{raw}」无效，用法：/插件列表 [页码|文本]")

    await finish_with_image(
        matcher,
        event,
        render_plugin_list(plugins, page=page),
        render_plugin_text(plugins, page=page),
    )


@info_matcher.handle()
async def _handle_info(event: Event, matcher: Matcher, args: Message = CommandArg()) -> None:
    keyword = args.extract_plain_text().strip()
    plugins = collect_plugins()

    if not keyword:
        await finish_with_image(
            matcher, event, render_plugin_list(plugins), render_plugin_text(plugins)
        )
        return

    info = await _resolve_plugin(matcher, keyword, plugins)
    if info is None:
        return

    await finish_with_image(
        matcher,
        event,
        render_plugin_detail(info),
        render_plugin_detail_text(info),
    )


@help_matcher.handle()
async def _handle_help(matcher: Matcher) -> None:
    await matcher.finish(HELP_TEXT)


@install_matcher.handle()
async def _handle_install(matcher: Matcher, args: Message = CommandArg()) -> None:
    await _manage_plugin(matcher, args, install=True)


@uninstall_matcher.handle()
async def _handle_uninstall(matcher: Matcher, args: Message = CommandArg()) -> None:
    await _manage_plugin(matcher, args, install=False)


async def _manage_plugin(matcher: Matcher, args: Message, *, install: bool) -> None:
    """安装 / 卸载插件的公共逻辑，真正的活儿交给 `src/utils`。"""

    name = args.extract_plain_text().strip()
    action = "安装" if install else "卸载"

    if not name:
        await matcher.finish(
            f"用法：/插件{action} <插件包名>，例如：/插件{action} nonebot-plugin-foo"
        )

    if (
        is_valid_plugin_name is None
        or plugin_install is None
        or plugin_uninstall is None
    ):
        await matcher.finish("当前部署方式下读不到 src/utils，无法在线安装 / 卸载插件。")

    if not is_valid_plugin_name(name):
        await matcher.finish(
            f"「{name}」不是合法的插件包名：只支持字母、数字和 `.` `_` `-`。"
        )

    # pip 可能要跑几十秒到几分钟，先回一条消息，免得用户以为机器人没反应
    await matcher.send(f"⏳ 正在{action} {name}，请稍候（pip 可能要花点时间）…")

    handler = plugin_install if install else plugin_uninstall
    ok, message = await handler(name)
    if ok:
        message = f"{message}\n重启机器人后生效，可用 /插件列表 查看。"
    await matcher.finish(message)
