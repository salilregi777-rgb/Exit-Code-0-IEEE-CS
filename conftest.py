"""Always isolate destructive test fixtures from the live competition database."""
import os
import sys
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
# Set before test modules import app/Config. Ignore DATABASE_PATH deliberately:
# existing fixtures call init_db(force_reset=True), including when pytest is run
# from a deployment shell that points DATABASE_PATH at real participant data.
_test_database_directory = tempfile.TemporaryDirectory(prefix="exitcode-pytest-")
os.environ["DATABASE_PATH"] = str(Path(_test_database_directory.name) / "competition.db")


def pytest_sessionfinish(session, exitstatus):
    _test_database_directory.cleanup()


@pytest.fixture
def ordered_bank(monkeypatch):
    """Use known fixtures for scoring cases; shuffle tests use real assignment."""
    import database
    monkeypatch.setattr(database, "balanced_question_order", lambda questions: sorted(questions, key=lambda q: q["id"]))
