from alembic import context

from backend.db import models  # noqa: F401
from backend.db.session import Base, engine

target_metadata = Base.metadata


def run_migrations_online():
    connectable = context.config.attributes.get("connection") or engine()
    if hasattr(connectable, "connect"):
        with connectable.connect() as conn:
            context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connectable, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
