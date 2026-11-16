from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings):
        url = settings.sql_url()
        sqlite = str(url).startswith("sqlite")
        self.engine = create_engine(
            url, pool_pre_ping=True, connect_args={"check_same_thread": False} if sqlite else {"connect_timeout": 5}
        )
        if sqlite:

            @event.listens_for(self.engine, "connect")
            def sqlite_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

        self.session = sessionmaker(self.engine, expire_on_commit=False)


def get_db(request: Request):
    with request.app.state.db.session() as session:
        yield session
