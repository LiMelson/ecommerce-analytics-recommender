from alembic import context
from sqlalchemy import create_engine, pool

from app.config import Settings
from app.db import Base
from app import models  # noqa: F401

target_metadata = Base.metadata


if context.is_offline_mode():
    context.configure(url=Settings().sql_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(Settings().sql_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
