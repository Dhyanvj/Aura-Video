from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app.utils import utils

_db_path = utils.storage_dir("", create=True) + "/aura.db"
engine = create_engine(f"sqlite:///{_db_path}", connect_args={"check_same_thread": False})


def init_db():
    # Import models so their tables are registered on SQLModel.metadata before creation.
    from app.db import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope():
    with Session(engine) as session:
        yield session
