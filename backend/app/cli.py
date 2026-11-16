import argparse
import secrets

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values, set_key
from sqlalchemy import create_engine, text

from .analytics import Analytics
from .auth import bootstrap
from .config import ROOT, Settings
from .db import Database
from .importing import queue_import, safe_inbox
from .models import AppSetting


def init_config():
    path = ROOT / ".env"
    if not path.exists():
        path.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    if not dotenv_values(path).get("CLICKHOUSE_PASSWORD"):
        set_key(path, "CLICKHOUSE_PASSWORD", secrets.token_urlsafe(24))
    print("Local configuration ready:", path)


def initialize(settings, mysql_only=False):
    if not settings.database_url:
        engine = create_engine(settings.sql_url(False), connect_args={"connect_timeout": 5})
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                )
            )
        engine.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    database = Database(settings)
    settings.instance_dir.mkdir(parents=True, exist_ok=True)
    (settings.instance_dir / "inbox").mkdir(exist_ok=True)
    password = secrets.token_urlsafe(18)
    with database.session() as db:
        created = bootstrap(db, password)
        for key in ("active_dataset", "active_model"):
            if not db.get(AppSetting, key):
                db.add(AppSetting(key=key, value=""))
        db.commit()
    if created:
        credentials = settings.instance_dir / "first-admin.txt"
        credentials.write_text(
            f"本地首次登录信息（不要提交或转发）\n账号：admin\n密码：{password}\n登录后可在个人信息中修改密码。\n",
            encoding="utf-8",
        )
        print("Initial admin created; credentials saved locally:", credentials)
    else:
        print("Existing admin preserved; password was not changed")
    if not mysql_only:
        Analytics(settings).init_schema()
        print("ClickHouse schema ready")
    print("MySQL schema ready")


def main():
    parser = argparse.ArgumentParser(description="Commerce Insights local administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-config")
    init = sub.add_parser("init")
    init.add_argument("--mysql-only", action="store_true")
    imp = sub.add_parser("import-file")
    imp.add_argument("relative_path", help="File within backend/instance/inbox")
    imp.add_argument("--name", default="")
    imp.add_argument("--demo", action="store_true")
    imp.add_argument("--no-activate", action="store_true")
    args = parser.parse_args()
    if args.command == "init-config":
        init_config()
        return
    settings = Settings()
    if args.command == "init":
        initialize(settings, args.mysql_only)
    else:
        path = safe_inbox(settings, args.relative_path)
        with Database(settings).session() as db:
            dataset, job = queue_import(db, "admin", path, args.name, not args.no_activate, args.demo)
            print("Queued dataset:", dataset.id, "job:", job.id)


if __name__ == "__main__":
    main()
