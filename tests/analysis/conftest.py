from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def broken_repo() -> Path:
    """A checkout where every analyser has something to find (see the file's comments)."""
    return FIXTURES / "analysis" / "broken_repo"
