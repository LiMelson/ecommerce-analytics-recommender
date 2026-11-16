import time
import uuid

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return int(time.time())


def uid():
    return uuid.uuid4().hex


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    permissions: Mapped[list] = mapped_column(JSON)


class Account(Base):
    __tablename__ = "accounts"
    username: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str] = mapped_column(String(254), default="")
    team: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.username"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(100), default="用户导入")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0)
    start_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.username"))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.username"))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    processed_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now)
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(30))
    module: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(20), default="成功")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now, index=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("accounts.username"))
    author_name: Mapped[str] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    name: Mapped[str] = mapped_column(String(100))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.username"))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


def audit(db, actor, module, action, detail="", status="成功"):
    db.add(AuditLog(actor=actor, module=module, action=action, detail=str(detail)[:1000], status=status))
