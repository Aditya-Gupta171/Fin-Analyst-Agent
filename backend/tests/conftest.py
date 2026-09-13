import pytest

from app.engine.catalog import Catalog
from app.paths import RULES_DIR


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return Catalog.load(RULES_DIR)
