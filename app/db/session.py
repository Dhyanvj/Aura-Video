from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app.utils import utils

_db_path = utils.storage_dir("", create=True) + "/aura.db"
engine = create_engine(f"sqlite:///{_db_path}", connect_args={"check_same_thread": False})

# Columns added to videoproject after its initial release. create_all() only
# creates missing tables, not missing columns on an existing table, so an
# upgrade from a pre-v2 database needs these added explicitly - this keeps
# existing projects opening normally instead of requiring a fresh DB.
_VIDEOPROJECT_NEW_COLUMNS = {
    "content_type_id": "TEXT",
    "quality_preset": "TEXT",
    "series_id": "INTEGER",
    "episode_number": "INTEGER",
    "research_evidence": "JSON",
}
_CONTENTTYPETEMPLATE_NEW_COLUMNS = {
    "description": "TEXT NOT NULL DEFAULT ''",
}


def _add_missing_columns(engine, table: str, columns: dict) -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, ddl_type in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
        conn.commit()


def init_db():
    # Import models so their tables are registered on SQLModel.metadata before creation.
    from app.db import models  # noqa: F401
    from app.db.seed import seed_content_types

    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine, "videoproject", _VIDEOPROJECT_NEW_COLUMNS)
    _add_missing_columns(engine, "contenttypetemplate", _CONTENTTYPETEMPLATE_NEW_COLUMNS)

    with Session(engine) as session:
        seed_content_types(session)


def get_session():
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope():
    with Session(engine) as session:
        yield session
