import pytest
from fastapi.testclient import TestClient

from tests.api_fixtures import make_client


@pytest.fixture
def client() -> TestClient:
    return make_client()
