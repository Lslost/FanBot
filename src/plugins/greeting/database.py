"""早安 / 晚安记录的存储（SQLModel + SQLite）。

表结构和上一版 `sqlite3` 实现一致（只有 `user_profile.user_id` 现在显式声明
`NOT NULL`），老数据库可以直接接着用：`checkin` 存每一次有效的早安 / 晚安打卡
（同一个群、同一天、同一个人、同一种打卡只会有一条），`user_profile` 存用户
昵称与性别，性别用来决定称呼（少女 / 少年 / 群友）。

并发控制沿用原来的思路：每个会话都在 `BEGIN IMMEDIATE` 事务里跑，
所以两个人同时打卡也不会算出相同的名次。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

from nonebot.log import logger
from sqlalchemy import Column, Index, Integer, Text, desc, event, func, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import get_config

__all__ = [
    "CheckIn",
    "CheckInRecord",
    "GreetingDatabase",
    "UserProfile",
    "default_db_path",
    "get_database",
]

Kind = Literal["morning", "night"]

ISO_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
"""打卡时间的存储格式，与旧版实现一致（ISO 文本，精确到秒）"""


class ISODateTime(TypeDecorator[datetime]):
    """按 ISO 文本存取 `datetime`，文本格式与旧版实现一致。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        return value.strftime(ISO_TIME_FORMAT)

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)


class CheckInRecord(SQLModel, table=True):
    """一次打卡记录。"""

    __tablename__ = "checkin"
    __table_args__ = (
        Index("idx_checkin_user_kind_time", "user_id", "kind", desc("created_at")),
        Index("idx_checkin_scope_kind_day", "scope_id", "kind", "day"),
    )

    scope_id: str = Field(sa_column=Column(Text, primary_key=True))
    """群号（group_xxx）或私聊（private_xxx）"""
    user_id: str = Field(sa_column=Column(Text, primary_key=True))
    kind: str = Field(sa_column=Column(Text, primary_key=True))
    """morning / night"""
    day: str = Field(sa_column=Column(Text, primary_key=True))
    """按分界小时归好的日期 YYYY-MM-DD"""
    rank: int = Field(sa_column=Column(Integer, nullable=False))
    """当天该范围内的名次"""
    nickname: str | None = Field(sa_column=Column(Text, nullable=True))
    gender: str | None = Field(sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(sa_column=Column(ISODateTime, nullable=False))


class UserProfile(SQLModel, table=True):
    """用户昵称与性别。"""

    __tablename__ = "user_profile"

    user_id: str = Field(sa_column=Column(Text, primary_key=True))
    nickname: str | None = Field(sa_column=Column(Text, nullable=True))
    gender: str = Field(
        sa_column=Column(Text, nullable=False, server_default=text("'unknown'"))
    )
    updated_at: datetime = Field(sa_column=Column(ISODateTime, nullable=False))


class CheckIn(SQLModel):
    """一次打卡的结果（不建表）。"""

    rank: int
    """今天这个群里的第几个"""
    created_at: datetime
    already: bool
    """今天是否已经打过同类型的卡"""


def default_db_path() -> Path:
    """获取数据库路径。"""

    config = get_config()
    if config.db_path:
        return Path(config.db_path).expanduser()

    try:
        from nonebot_plugin_localstore import get_plugin_data_dir
    except ImportError:
        return Path.cwd() / "data" / "greeting" / "greeting.db"
    return get_plugin_data_dir() / "greeting.db"


class GreetingDatabase:
    """早安 / 晚安记录（SQLModel + SQLite）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = self._make_engine(self.path)
        self._init_schema()

    @staticmethod
    def _make_engine(path: Path) -> Engine:
        engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(path)),
            # timeout 是 sqlite3 的忙等待时长，等别人写完再抢锁
            connect_args={"timeout": 10, "check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _disable_implicit_transaction(
            dbapi_connection: object, _record: object
        ) -> None:
            # 关掉 pysqlite 自己发的 BEGIN，事务改由下面显式控制
            dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

        @event.listens_for(engine, "begin")
        def _begin_immediate(conn: object) -> None:
            # 打卡要先算名次再写入，读之前就得拿到写锁，否则并发时会算出同名次
            conn.exec_driver_sql("BEGIN IMMEDIATE")  # type: ignore[attr-defined]

        return engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        """开启一个事务，正常结束时提交，出错时回滚。"""

        # expire_on_commit=False：提交后对象上的字段仍然可以读，不用再多查一次库
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def _init_schema(self) -> None:
        try:
            SQLModel.metadata.create_all(self.engine)
        except SQLAlchemyError as error:
            logger.opt(exception=error).error(f"greeting: 初始化数据库 {self.path} 失败")

    def check_in(
        self,
        *,
        scope_id: str,
        user_id: str,
        kind: Kind,
        day: str,
        nickname: str | None,
        gender: str | None,
        now: datetime,
    ) -> CheckIn:
        """记录一次打卡，返回名次（重复打卡返回原名次）。"""

        with self.session() as session:
            existing = session.exec(
                select(CheckInRecord).where(
                    CheckInRecord.scope_id == scope_id,
                    CheckInRecord.user_id == user_id,
                    CheckInRecord.kind == kind,
                    CheckInRecord.day == day,
                )
            ).one_or_none()

            self._save_profile(session, user_id, nickname, gender, now)

            if existing is not None:
                return CheckIn(
                    rank=existing.rank,
                    created_at=existing.created_at,
                    already=True,
                )

            count = session.exec(
                select(func.count())
                .select_from(CheckInRecord)
                .where(
                    CheckInRecord.scope_id == scope_id,
                    CheckInRecord.kind == kind,
                    CheckInRecord.day == day,
                )
            ).one()
            rank = int(count) + 1

            session.add(
                CheckInRecord(
                    scope_id=scope_id,
                    user_id=user_id,
                    kind=kind,
                    day=day,
                    rank=rank,
                    nickname=nickname,
                    gender=gender,
                    created_at=now,
                )
            )
            return CheckIn(rank=rank, created_at=now, already=False)

    def last_time(self, user_id: str, kind: Kind) -> datetime | None:
        """某个用户最近一次打卡时间（不区分群，用来算睡眠时长）。"""

        with self.session() as session:
            return session.exec(
                select(CheckInRecord.created_at)
                .where(CheckInRecord.user_id == user_id, CheckInRecord.kind == kind)
                .order_by(desc(CheckInRecord.created_at))
                .limit(1)
            ).one_or_none()

    def time_of_day(
        self, *, scope_id: str, user_id: str, kind: Kind, day: str
    ) -> datetime | None:
        """某个用户今天在这个群的某种打卡时间。"""

        with self.session() as session:
            return session.exec(
                select(CheckInRecord.created_at).where(
                    CheckInRecord.scope_id == scope_id,
                    CheckInRecord.user_id == user_id,
                    CheckInRecord.kind == kind,
                    CheckInRecord.day == day,
                )
            ).one_or_none()

    def gender_of(self, user_id: str) -> str | None:
        """读取之前记录过的性别，未知时返回 None。"""

        with self.session() as session:
            profile = session.get(UserProfile, user_id)
            gender = profile.gender if profile is not None else None
        return gender if gender in {"male", "female"} else None

    @staticmethod
    def _save_profile(
        session: Session,
        user_id: str,
        nickname: str | None,
        gender: str | None,
        updated_at: datetime,
    ) -> None:
        """更新用户资料；性别未知时不会覆盖已经记录过的性别。"""

        profile = session.get(UserProfile, user_id)
        if profile is None:
            session.add(
                UserProfile(
                    user_id=user_id,
                    nickname=nickname,
                    gender=gender or "unknown",
                    updated_at=updated_at,
                )
            )
            return

        if nickname:
            profile.nickname = nickname
        if gender in {"male", "female"}:
            profile.gender = gender
        profile.updated_at = updated_at
        session.add(profile)


_database: GreetingDatabase | None = None
_database_lock = threading.Lock()


def get_database() -> GreetingDatabase:
    """获取全局唯一的数据库实例。"""

    global _database

    with _database_lock:
        if _database is None:
            _database = GreetingDatabase()
        return _database
