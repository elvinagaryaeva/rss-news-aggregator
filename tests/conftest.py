import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["DEMO_RSS_FEEDS"] = ""
os.environ["INTERNAL_BASE_URL"] = "http://testserver"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"

import pytest
from sqlalchemy.orm import Session

from app.db.session import Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def database() -> Session:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def sample_rss() -> bytes:
    with open("tests/fixtures/sample_feed.xml", "rb") as fixture:
        return fixture.read()
