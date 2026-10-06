"""机器人与插件管理工具：退出进程、安装 / 卸载插件。

安装 / 卸载走 `nb-cli`（等价于在项目根目录执行 `nb plugin install <包名>`），
命令跑完后再把插件模块名同步进 `plugin_index.json` —— `run.py` 用
`nonebot.load_from_json()` 读它加载插件，所以同步之后重启即可生效。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
from importlib import invalidate_caches, metadata
from pathlib import Path
from typing import Sequence

from nonebot import logger

__all__ = [
    "PLUGIN_INDEX_PATH",
    "PROJECT_ROOT",
    "is_valid_plugin_name",
    "plugin_install",
    "plugin_uninstall",
    "shutdown",
]

PLUGIN_INDEX_KEY = "plugins"
"""`plugin_index.json` 里存放插件模块名的键名。"""

NB_COMMAND_TIMEOUT = 300.0
"""`nb` 命令的超时时间（秒），pip 下载太慢时不至于一直挂着。"""

MAX_OUTPUT_CHARS = 800
"""回给用户的命令输出最多保留多少字符。"""

# 只允许字母、数字和 . _ -，避免用户输入的包名被当成命令行参数（注入）
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

# pip 同一时间只跑一个，避免两次安装互相踩到 pyproject.toml / 环境
_INSTALL_LOCK = asyncio.Lock()


def _project_root() -> Path:
    """项目根目录（`pyproject.toml` 所在目录），拿不准时退回当前工作目录。"""

    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd()


PROJECT_ROOT = _project_root()
"""机器人项目根目录，`nb` 命令都在这里执行。"""

PLUGIN_INDEX_PATH = PROJECT_ROOT / "plugin_index.json"
"""`nonebot.load_from_json()` 读取的插件清单。"""


def shutdown() -> None:
    """给当前进程发 SIGINT，交给 systemd / nb-cli 之类的进程管理器重启。"""

    logger.info("正在退出当前进程...")
    os.kill(os.getpid(), signal.SIGINT)


def is_valid_plugin_name(name: str) -> bool:
    """校验插件包名：只允许字母、数字和 `.` `_` `-`。"""

    name = name.strip()
    return bool(name) and _PLUGIN_NAME_RE.match(name) is not None


async def plugin_install(plugin_name: str) -> tuple[bool, str]:
    """安装插件，返回 `(是否成功, 给用户看的说明)`。"""

    plugin_name = plugin_name.strip()
    if not is_valid_plugin_name(plugin_name):
        return False, f"「{plugin_name}」不是合法的插件包名，只支持字母、数字和 . _ -"

    async with _INSTALL_LOCK:
        if _is_dist_installed(plugin_name):
            return False, (
                f"⚠️ {plugin_name} 已经装过了，想升级请用 `nb plugin update {plugin_name}`。"
            )

        code, output = await _run_nb("plugin", "install", "--", plugin_name)
        if code != 0:
            logger.warning(
                f"安装插件 {plugin_name} 失败（退出码 {code}）\n{output}"
            )
            return False, f"❌ 安装 {plugin_name} 失败：\n{_tail(output)}"

        modules = _module_names_of(plugin_name)
        note = _sync_index(added=modules)
        logger.info(f"已安装插件 {plugin_name}（模块 {modules}）")

    return True, f"✅ 已安装 {plugin_name}\n插件模块：{'、'.join(modules)}{note}"


async def plugin_uninstall(plugin_name: str) -> tuple[bool, str]:
    """卸载插件，返回 `(是否成功, 给用户看的说明)`。"""

    plugin_name = plugin_name.strip()
    if not is_valid_plugin_name(plugin_name):
        return False, f"「{plugin_name}」不是合法的插件包名，只支持字母、数字和 . _ -"

    async with _INSTALL_LOCK:
        # 模块名要在卸载之前查，卸完 importlib.metadata 就找不到这个包了
        modules = _module_names_of(plugin_name)

        code, output = await _run_nb("plugin", "uninstall", "--", plugin_name)
        if code != 0:
            logger.warning(
                f"卸载插件 {plugin_name} 失败（退出码 {code}）\n{output}"
            )
            return False, f"❌ 卸载 {plugin_name} 失败：\n{_tail(output)}"

        # 卸载不存在的插件时 nb-cli 只打印一行提示、退出码仍是 0，这里再确认一次
        if _is_dist_installed(plugin_name):
            return False, f"⚠️ 没有卸载 {plugin_name}：\n{_tail(output)}"

        note = _sync_index(removed=modules)
        logger.info(f"已卸载插件 {plugin_name}（模块 {modules}）")

    return True, f"✅ 已卸载 {plugin_name}\n插件模块：{'、'.join(modules)}{note}"


async def _run_nb(*args: str) -> tuple[int, str]:
    """执行 `nb` 命令并回收输出，超时 / 找不到 nb-cli 时返回非零状态码。"""

    command = [
        sys.executable,
        "-m",
        "nb_cli",
        "-d",
        str(PROJECT_ROOT),
        "-py",
        sys.executable,
        *args,
    ]
    logger.info(f"执行 nb 命令：nb {' '.join(args)}")

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), NB_COMMAND_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        process.kill()
        await process.wait()
        return 124, f"命令执行超过 {NB_COMMAND_TIMEOUT:.0f} 秒还没结束，已经中断。"
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise

    return process.returncode or 0, (stdout or b"").decode("utf-8", errors="replace")


def _is_dist_installed(dist_name: str) -> bool:
    """判断某个 pip 包是否已经装在当前环境里。"""

    invalidate_caches()
    try:
        metadata.distribution(dist_name)
    except metadata.PackageNotFoundError:
        return False
    except Exception as error:  # pragma: no cover - 环境异常时按「没装」处理
        logger.opt(exception=error).debug(f"查询 {dist_name} 安装状态失败")
        return False
    return True


def _normalize_dist_name(name: str) -> str:
    """按 PEP 503 归一化包名，`nonebot_plugin_x` 与 `nonebot-plugin-x` 视为同一个。"""

    return name.strip().lower().replace("_", "-")


def _module_names_of(dist_name: str) -> list[str]:
    """反查 pip 包名对应的插件模块名。

    优先用 `importlib.metadata.packages_distributions()` 拿到包真实提供的顶层模块，
    查不到时按惯例把 `nonebot-plugin-x` 猜成 `nonebot_plugin_x`。
    """

    wanted = _normalize_dist_name(dist_name)
    modules: list[str] = []

    try:
        mapping = metadata.packages_distributions()
    except Exception as error:  # pragma: no cover - 极端环境问题
        logger.opt(exception=error).debug("读取已安装包信息失败")
        mapping = {}

    for module, distributions in mapping.items():
        if not module or module.startswith("_"):
            continue
        if any(_normalize_dist_name(dist) == wanted for dist in distributions):
            if module not in modules:
                modules.append(module)

    if not modules:
        return [dist_name.strip().replace("-", "_")]

    preferred = [module for module in modules if module.startswith("nonebot_plugin")]
    return preferred or modules


def _read_index() -> dict | None:
    """读取 `plugin_index.json`，文件不存在时返回空字典，读坏时返回 `None`。"""

    if not PLUGIN_INDEX_PATH.is_file():
        return {}
    try:
        data = json.loads(PLUGIN_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.opt(exception=error).warning("读取 plugin_index.json 失败")
        return None
    return data if isinstance(data, dict) else None


def _write_index(data: dict) -> bool:
    """先写临时文件再替换，避免中途失败把清单写坏。"""

    temp = PLUGIN_INDEX_PATH.with_name(f"{PLUGIN_INDEX_PATH.name}.tmp")
    try:
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temp, PLUGIN_INDEX_PATH)
    except OSError as error:
        logger.opt(exception=error).warning("写入 plugin_index.json 失败")
        return False
    return True


def _sync_index(added: Sequence[str] = (), removed: Sequence[str] = ()) -> str:
    """把模块名同步进 `plugin_index.json`，返回给用户看的补充说明。"""

    data = _read_index()
    if data is None:
        return "\n（plugin_index.json 读取失败，没能同步，请手动检查）"

    modules = data.get(PLUGIN_INDEX_KEY)
    if not isinstance(modules, list):
        modules = []
    original = list(modules)

    modules = [name for name in modules if name not in set(removed)]
    for name in added:
        if name not in modules:
            modules.append(name)

    if modules == original:
        return ""

    data[PLUGIN_INDEX_KEY] = modules
    if not _write_index(data):
        return "\n（plugin_index.json 写入失败，请手动同步）"
    return f"\n已同步 plugin_index.json 的插件列表（共 {len(modules)} 个）。"


def _tail(output: str, max_lines: int = 5) -> str:
    """取命令输出的最后几行，去掉空行与 traceback，再限制总长度。"""

    raw = [line.rstrip() for line in (output or "").splitlines() if line.strip()]
    if not raw:
        return "（命令没有输出）"

    lines = _drop_traceback(raw) or raw[-1:]
    text = "\n".join(lines[-max_lines:])
    if len(text) > MAX_OUTPUT_CHARS:
        text = "…" + text[-MAX_OUTPUT_CHARS:]
    return text


def _drop_traceback(lines: Sequence[str]) -> list[str]:
    """丢掉 Python traceback 部分，只留下真正给用户看的报错。

    nb-cli 遇到找不到的插件时会直接抛异常，整段堆栈贴到聊天里没法看，
    所以从 `Traceback (most recent call last):` 开始的内容一律截掉。
    """

    kept: list[str] = []
    for line in lines:
        if line.startswith("Traceback (most recent call last):"):
            break
        if line.startswith(
            ("During handling of the above exception", "The above exception was")
        ):
            break
        kept.append(line)
    return kept
