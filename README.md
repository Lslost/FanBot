# FanBot

基于 [NoneBot2](https://nonebot.dev) + OneBot v11 的 QQ 机器人，Python 3.10 及以上都能跑。

## 功能

| 插件 | 说明 |
| --- | --- |
| OneBot v11 适配器 | 连接 QQ 协议端（NapCat / Lagrange / go-cqhttp 等） |
| parser | 解析 B 站 / 抖音 / 微博 / 小红书 / 推特 等链接，直接发原视频、图片和文案 |
| llmchat | 大模型聊天，支持私聊、群聊触发、随机插话、MCP 工具调用 |
| maimaidx | 舞萌 DX 查分、绑定、猜歌、别名推送等 |
| memes | 表情包生成 |
| arcaea-sticker | Arcaea 表情包自动回复 |
| apscheduler | 定时任务 |
| greeting | 本地插件：群友早安 / 晚安打卡、名次与睡眠时长 |
| plugin_manager | 本地插件：在聊天里查看 / 安装 / 卸载插件 |

## 环境要求

- Python 3.10 ~ 3.14
- 一个 OneBot v11 协议端
- Linux 上跑图片渲染（arcaea-sticker 走 htmlrender）需要 Chromium 及其系统依赖
- 中文字体（plugin_manager 画中文用，缺省会自动找 Noto Sans CJK / 思源黑体 / 文泉驿）

## 部署指南

### 1. 拉代码、装依赖

```bash
git clone <repo-url> fanbot
cd fanbot

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

`requirements.txt` 里每个包都写了用途，以及 maimaidx、htmlrender 那几个版本坑的来龙去脉，出问题可以先看那里的注释。不想用 venv 的话跳过前两步直接 `pip install` 也行。

### 2. 装 Playwright 浏览器

htmlrender 靠 Playwright 驱动 Chromium 渲染图片：

```bash
playwright install chromium
playwright install-deps chromium   # 装系统依赖，第一次需要 sudo
```

### 3. 配置 .env

```bash
cp .env.example .env
```

`.env.example` 里每一项都有注释，说明哪些必填、哪些不填就退回默认行为。最少要改这几项：

- `SUPERUSERS` —— 超级用户 QQ 号（JSON 数组，如 `["10001"]`）
- `MAIMAIDX_PATH` —— maimaidx 静态资源目录，必须是**绝对路径**且指向 `static`
- `LLMCHAT__API_PRESETS` —— 大模型的 `api_key` / `model_name` / `api_base`，不填 llmchat 会加载失败
- `NICKNAME` —— 机器人昵称（maimaidx 的称呼默认取这里的第一个）

常用的还有：

- `parser_bili_ck` —— B 站 cookie，必须含 `SESSDATA`，否则 B 站视频解析不了
- `DIVINGFISH_CLIENT_ID` / `DIVINGFISH_CLIENT_SECRET` —— 水鱼查分器的 OAuth 应用，填了才走 OAuth 授权
- `RENDER_PLAYWRIGHT__EXECUTABLE_PATH` —— 服务器上 chromium 的真实路径，默认值是 `/usr/bin/chromium-browser`

两个容易踩的坑：不想要的配置项请**整行注释掉**，不要写 `XXX=None`（会被当成字符串 `"None"`）；也别写「空值 + 行内注释」。

### 4. 连协议端

- **反向 WS**：协议端连到 `ws://<机器人IP>:<PORT>/onebot/v11/ws`，对得上 `ONEBOT_V11_ACCESS_TOKEN`
- **正向 WS**：机器人主动连协议端时配 `ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]`

`HOST` / `PORT` 在 `.env` 里改，默认 `127.0.0.1:3939`。

## 启动方法

二选一，两种方式都能正常跑。

### python run.py

```bash
python run.py
```

入口是 `run.py`：注册 OneBot v11 适配器，再按 `plugin_index.json` 的清单加载插件。`plugin_manager` 的 `/插件安装`、`/插件卸载` 会把新插件的模块名同步进这个清单，所以用这个入口时，装完插件重启就能生效。

### nb run

```bash
nb run

# 开发时热重载
nb run --reload
```

`nb` 命令由 nb-cli 提供（已经写在 `requirements.txt` 里）。它读 `pyproject.toml` 的 `[tool.nonebot]`，插件列表来自 `[tool.nonebot.plugins]`，插件目录是 `[tool.nonebot.plugin_dirs]`。如果 `nb` 不在 PATH 里，用 `python -m nb_cli run` 等价替代。

> 两个入口不要同时跑，会抢同一个端口和数据库。日常固定用一个就行；换了入口之后，插件清单以新入口的配置为准。

### 后台常驻（systemd）

`/etc/systemd/system/fanbot.service`：

```ini
[Unit]
Description=FanBot
After=network.target

[Service]
Type=simple
User=<你的用户>
WorkingDirectory=/path/to/fanbot
ExecStart=/path/to/fanbot/.venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fanbot
sudo journalctl -u fanbot -f
```

## 目录结构

```
run.py             启动入口（python run.py）
pyproject.toml     项目元信息 + [tool.nonebot] 配置（nb run 用）
plugin_index.json  run.py 读的插件清单
src/plugins/       本地插件：greeting、plugin_manager
src/utils/         本地工具：退出进程、插件安装 / 卸载
data/              localstore 数据目录（数据库 / 缓存），LOCALSTORE_USE_CWD=true 时生成在项目根目录
.env.example       配置模板，复制成 .env 后修改
```
