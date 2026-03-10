import pytest

from main import app as flask_app


@pytest.fixture
def app():
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def shodan_key():
    return "test-key-abcdef1234567890"


@pytest.fixture
def shodan_headers(shodan_key):
    return {"X-Shodan-Key": shodan_key}
