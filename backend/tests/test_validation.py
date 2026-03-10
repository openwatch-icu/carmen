"""Tests for input validation on API endpoints."""

import pytest


class TestStreamIdValidation:
    """stream_id must be a 32-char hex string (uuid4.hex)."""

    def test_valid_stream_id(self, client):
        resp = client.get(
            "/api/stream/abcdef1234567890abcdef1234567890"
            "/playlist.m3u8"
        )
        # 404 = valid format but stream doesn't exist
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "stream_id",
        [
            "short",
            "ABCDEF1234567890ABCDEF1234567890",  # uppercase
            "abcdef1234567890abcdef123456789",  # 31 chars
            "abcdef1234567890abcdef12345678900",  # 33 chars
            "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",  # non-hex
        ],
    )
    def test_invalid_stream_id_get(self, client, stream_id):
        resp = client.get(
            f"/api/stream/{stream_id}/playlist.m3u8"
        )
        assert resp.status_code == 400

    def test_path_traversal_rejected(self, client):
        resp = client.get(
            "/api/stream/../../../etc/passwd/playlist.m3u8"
        )
        # Flask router rejects this before our handler
        assert resp.status_code in (400, 404)

    @pytest.mark.parametrize(
        "stream_id",
        [
            "short",
            "not-a-valid-hex-stream-id-value!",
        ],
    )
    def test_invalid_stream_id_delete(self, client, stream_id):
        resp = client.delete(f"/api/stream/{stream_id}")
        assert resp.status_code == 400


class TestLocationValidation:
    def test_location_too_short(self, client, shodan_headers):
        resp = client.post(
            "/api/search",
            json={"location": "x"},
            headers=shodan_headers,
        )
        assert resp.status_code == 400

    def test_location_too_long(self, client, shodan_headers):
        resp = client.post(
            "/api/search",
            json={"location": "a" * 201},
            headers=shodan_headers,
        )
        assert resp.status_code == 400

    def test_location_empty(self, client, shodan_headers):
        resp = client.post(
            "/api/search",
            json={"location": ""},
            headers=shodan_headers,
        )
        assert resp.status_code == 400


class TestPortValidation:
    def test_proxy_invalid_port_string(self, client, monkeypatch):
        monkeypatch.setenv("PROXY_ENABLED", "true")
        resp = client.get(
            "/api/proxy/image?ip=8.8.8.8&port=abc"
        )
        assert resp.status_code == 400

    def test_proxy_port_zero(self, client, monkeypatch):
        monkeypatch.setenv("PROXY_ENABLED", "true")
        resp = client.get(
            "/api/proxy/image?ip=8.8.8.8&port=0"
        )
        assert resp.status_code == 400

    def test_proxy_port_too_high(self, client, monkeypatch):
        monkeypatch.setenv("PROXY_ENABLED", "true")
        resp = client.get(
            "/api/proxy/image?ip=8.8.8.8&port=70000"
        )
        assert resp.status_code == 400


class TestRadiusValidation:
    def test_radius_too_small(self, client, shodan_headers):
        resp = client.post(
            "/api/search",
            json={
                "location": "New York",
                "radius_miles": 0.1,
            },
            headers=shodan_headers,
        )
        assert resp.status_code == 400

    def test_radius_too_large(self, client, shodan_headers):
        resp = client.post(
            "/api/search",
            json={
                "location": "New York",
                "radius_miles": 100,
            },
            headers=shodan_headers,
        )
        assert resp.status_code == 400
