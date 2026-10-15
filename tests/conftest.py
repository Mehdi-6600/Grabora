import os
import tempfile
from pathlib import Path

import pytest

# Configure environment before importing application modules.
_tmp = tempfile.mkdtemp()
os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "test.db")
os.environ["TEMP_DIR"] = str(Path(_tmp) / "tmp")
os.environ["LOG_FILE"] = str(Path(_tmp) / "test.log")


@pytest.fixture
async def db():
    from database.db import close_db, init_db

    await init_db()
    yield
    await close_db()
