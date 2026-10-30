from alembic import context

from app.config import Settings
from app.database import Database
from app.models import Base

config = context.config
target_metadata = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=Settings().database_url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={'paramstyle':'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    provided = config.attributes.get('connection')
    if provided is not None:
        run(provided)
    else:
        database = Database(Settings())
        try:
            with database.engine.connect() as connection:
                run(connection)
        finally:
            database.close()
