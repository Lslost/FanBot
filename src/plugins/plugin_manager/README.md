# plugin_manager —— 插件管理

在聊天里查看机器人已安装的全部插件（图片形式）、单个插件的详情，
以及在线安装 / 卸载插件。

## 指令

| 指令 | 说明 | 权限 |
| --- | --- | --- |
| `/插件列表 [页码\|文本]` | 以图片列出全部插件，可翻页；加 `文本` 参数时发送纯文本 | 所有人 |
| `/插件信息 [名称]` | 以图片展示插件详情（简介、用法、版本、模块名、响应器数量等）；不带名称时等同于 `/插件列表` | 所有人 |
| `/插件安装 <包名>` | 安装插件，例如 `/插件安装 nonebot-plugin-foo` | 超级用户 |
| `/插件卸载 <包名>` | 卸载插件，例如 `/插件卸载 nonebot-plugin-foo` | 超级用户 |
| `/插件帮助` | 显示帮助 | 所有人 |

别名：`插件管理` / `plugin_list` / `plist`、`plugin_info`、`plugin_help`、
`安装插件` / `plugin_install`、`卸载插件` / `plugin_uninstall`。
名称支持模糊匹配，匹配到多个时会列出候选。

## 实现方式

* **插件发现**：同时列出已加载的插件、`PLUGIN_MANAGER__PLUGIN_DIRS` 目录下尚未加载的本地插件，
  以及 pip 安装的 `nonebot-plugin-*` 第三方插件（`importlib.metadata` 扫描，带 60 秒缓存）。
  列表里的「已加载 / 未加载」只反映当前进程状态，插件是否加载由启动配置决定。
* **图片渲染**：Pillow 直接绘制（渐变头部、状态标签、来源标签、圆角卡片），
  不依赖 htmlrender / 浏览器 / 网络。适配器不支持图片时（例如 console 适配器）自动回退为文本。
* **安装 / 卸载**：调用 `src/utils/bot_manage.py`，实际执行
  `python -m nb_cli plugin install|uninstall <包名>`（等价于在项目根目录跑 `nb plugin ...`）。
  pip 要在环境里装包，所以可能要等几十秒到几分钟；成功后会把插件模块名写进
  `plugin_index.json`，`pyproject.toml` 的 `[tool.nonebot.plugins]` 由 nb-cli 维护。
  **装上 / 卸掉之后需要重启机器人**（`nb` 或 systemd 会重新加载）才生效。
  同一时间只允许一个安装 / 卸载任务，命令超时 300 秒。

## 权限

安装 / 卸载只对 `.env` 里 `SUPERUSERS` 列出的用户开放（NoneBot 的 `SUPERUSER` 权限）。
`SUPERUSERS` 为空时任何人都用不了这两个命令。

## 配置

配置写在 `.env` 中，前缀为 `PLUGIN_MANAGER__`：

```dotenv
# 插件列表每页显示的插件数量（默认 9）
PLUGIN_MANAGER__PAGESIZE=9
# 需要扫描的本地插件目录
PLUGIN_MANAGER__PLUGIN_DIRS=["src/plugins"]
# 自定义中文字体（默认自动查找系统字体）
PLUGIN_MANAGER__FONT_PATH=
PLUGIN_MANAGER__FONT_BOLD_PATH=
# 是否显示 NoneBot 内置插件
PLUGIN_MANAGER__SHOW_BUILTIN=true
```

## 依赖

需要 `pillow`（图片渲染）与支持中文的字体。字体默认自动查找
Noto Sans CJK / 思源黑体 / 文泉驿，找不到时可用 `PLUGIN_MANAGER__FONT_PATH` 指定。
安装 / 卸载还需要 `nb-cli`（已写进 `requirements.txt`），以及能访问 NoneBot
插件商店 / PyPI 的网络。
