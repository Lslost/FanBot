# greeting —— 早安晚安

记录群友的早安 / 晚安打卡，并汇报打卡名次与睡眠时长。数据存在本地 SQLite 里，
用 SQLModel（底层 SQLAlchemy）读写，需要 `sqlmodel` 依赖。

## 指令

| 指令 | 别名 | 说明 |
| --- | --- | --- |
| `早安` | `早上好`、`morning` | 打早安卡 |
| `晚安` | `好梦`、`goodnight` | 打晚安卡 |

回复示例：

```
@小樱 🌅 早安！你是今天第 3 个早安的少女。
😴 从 23:40 睡到 07:05，一共睡了 7 小时 25 分钟。
```

```
@小明 🌙 晚安！你是今天第 2 个晚安的少年。
🌅 今天你 07:02 就起床了，快好好休息吧～
```

## 行为说明

* **称呼方式**：群聊里回复会 @ 一下发送者（拼 `MessageSegment.at()`）；私聊里 @ 没有意义，
  或者适配器不支持 @ 时，就退回写成昵称，例如「🌅 早安，小樱！你是今天第 3 个早安的少女。」。
  这与 OneBot `bot.send(..., at_sender=True)` 只在群聊生效的行为一致。
* **名次**：按「同一个群 + 同一天 + 同一种打卡」统计，同一个群友当天只计一次；
  重复打卡会告诉他原本的名次（不会占用新的名次）。
* **称呼**：取得到性别时为「少女 / 少年」，取不到时叫「群友」。
  性别优先来自消息事件（例如 OneBot v11 的 `sender.sex`）；事件里没有时，
  群聊会再调用 `get_group_member_info` 查询群成员资料。取到过就会记进数据库，
  以后即使事件和接口暂时没有性别信息也还能沿用。
* **睡眠时长**：早安时用「上一次晚安的时间」到现在计算（不区分群，跨群也认）；
  没有晚安记录就提醒他今晚早点睡，超过 20 小时则只提示作息。
* **一天的分界**：默认凌晨 4 点前算前一天，避免凌晨两三点说「早安 / 晚安」被算成新的一天。
* **限时打卡**：默认早安卡只在 `04:00–11:00` 有效、晚安卡只在 `21:00–06:00`（跨零点）有效，
  不在时段内会提示「现在不是早安/晚安时间哦」并且**不记入打卡**。时间段可以改，
  留空或起止写成相同的时间就表示不限时（见下方「配置」）。
* **私聊**：私聊里同样可用，此时名次按自己算。

## 数据库

默认位置 `data/greeting/greeting.db`（安装了 `nonebot-plugin-localstore` 时用它提供的数据目录），
可用 `GREETING__DB_PATH` 指定。表结构由 SQLModel 的模型定义（见 `database.py`），
启动时用 `create_all` 建表。表结构和旧版 `sqlite3` 实现一致
（只有 `user_profile.user_id` 现在显式声明 `NOT NULL`），旧库可以直接接着用。

两张表（`CheckInRecord` / `UserProfile`）：

* `checkin`：`scope_id`（群号 `group_xxx` 或私聊 `private_xxx`）、`user_id`、`kind`
  （morning / night）、`day`（按分界小时归好的日期 YYYY-MM-DD）、`rank`（当天该范围内的名次）、
  `nickname`、`gender`、`created_at`（本机时区的本地时间，ISO 文本），
  主键 `(scope_id, user_id, kind, day)`。
* `user_profile`：`user_id`（主键）、`nickname`、`gender`（默认 `unknown`）、`updated_at`。

`created_at` / `updated_at` 用自定义类型 `ISODateTime` 存取 ISO 文本，和旧版的数据格式一致。
并发方面每个会话都跑在 `BEGIN IMMEDIATE` 事务里，同时打卡不会算出相同名次。

## 配置

```dotenv
# 一天的分界小时，默认 4 点
GREETING__DAY_BOUNDARY_HOUR=4
# 打卡时段，留空表示不限时（结束时间早于开始时间表示跨零点）
GREETING__MORNING_START=04:00
GREETING__MORNING_END=11:00
GREETING__NIGHT_START=21:00
GREETING__NIGHT_END=06:00
# SQLite 数据库路径
GREETING__DB_PATH=
```

## 已知限制

* 名次按用户去重，同一人一天只算一次；如果希望「说几次算几次」，需要改成按消息计数。
* 睡眠时长以上一次晚安为准，午睡也会被算进去（`晚安`→`早安` 之间不足 20 小时都会当成一觉）。
* 打卡时段用的是机器人所在机器的本地时间，时段设置对所有人一致，不能按人区分。
* 打卡记录不会自动清理，长期使用建议偶尔清理 `checkin` 里的旧数据。
