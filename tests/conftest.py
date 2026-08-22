from __future__ import annotations

from pathlib import Path

import pytest

from app.analytics import AnalyticsEngine
from app.data_generator import generate_dataset


@pytest.fixture(scope="session")
def generated_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("synthetic-data")
    generate_dataset(output)
    return output


@pytest.fixture(scope="session")
def engine(generated_dir: Path) -> AnalyticsEngine:
    return AnalyticsEngine(generated_dir)
