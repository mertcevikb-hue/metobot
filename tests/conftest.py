import os
import pytest
from pathlib import Path
from sqlalchemy import create_engine
from database.schema import Base

# Ensure tests run against an isolated test database, NEVER touching the live production database
TEST_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_suite.db"))
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"

# Override DATABASE_URL before importing web.api or database.db_manager
os.environ["DATABASE_URL"] = TEST_DB_URL


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Ensure isolated test database file exists with all tables created."""
    os.environ["DATABASE_URL"] = TEST_DB_URL
    
    # Initialize all tables in test_suite.db
    sync_engine = create_engine(f"sqlite:///{TEST_DB_PATH}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    
    # Point web.api.db_manager to test database as well
    try:
        from database.db_manager import DatabaseManager
        import web.api
        web.api.db_manager = DatabaseManager(TEST_DB_URL)
    except Exception:
        pass

    yield
    
    # Cleanup test db file after test session
    for ext in ["", "-wal", "-shm"]:
        p = f"{TEST_DB_PATH}{ext}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


@pytest.fixture
def db():
    """Provide an isolated DatabaseManager pointing to test_suite.db."""
    from database.db_manager import DatabaseManager
    return DatabaseManager(db_url=TEST_DB_URL)
