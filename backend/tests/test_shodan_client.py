"""Tests for Shodan client parsing and query logic."""

import httpx
import pytest
import respx

from shodan_client import (
    SHODAN_SEARCH_URL,
    ShodanClient,
    _classify_brand,
    _parse_match,
)


class TestClassifyBrand:
    def test_hikvision(self):
        assert _classify_brand({"product": "Hikvision"}) == "Hikvision"

    def test_dahua_in_data(self):
        assert (
            _classify_brand({"data": "Server: Dahua"})
            == "Dahua"
        )

    def test_mjpg_streamer(self):
        assert (
            _classify_brand({"info": "MJPG-Streamer"})
            == "MJPG-Streamer"
        )

    def test_unknown(self):
        assert _classify_brand({"product": "nginx"}) == "Unknown"

    def test_empty(self):
        assert _classify_brand({}) == "Unknown"

    def test_none_fields(self):
        assert (
            _classify_brand(
                {"product": None, "info": None, "data": None}
            )
            == "Unknown"
        )


class TestParseMatch:
    def test_basic_match(self):
        match = {
            "ip_str": "1.2.3.4",
            "port": 80,
            "product": "yawcam",
            "version": "0.6",
            "org": "TestISP",
            "hostnames": ["cam.example.com"],
            "transport": "tcp",
            "location": {
                "latitude": 40.7,
                "longitude": -74.0,
                "city": "New York",
                "country_name": "United States",
            },
            "timestamp": "2025-01-01T00:00:00",
            "tags": [],
        }
        result = _parse_match(match, 40.7, -74.0)
        assert result["ip"] == "1.2.3.4"
        assert result["port"] == 80
        assert result["brand"] == "Yawcam"
        assert result["distance_km"] == 0.0
        assert result["thumbnail_url"] is not None

    def test_missing_location(self):
        match = {"ip_str": "1.2.3.4", "port": 8080}
        result = _parse_match(match, 40.7, -74.0)
        assert result["lat"] is None
        assert result["lng"] is None
        assert result["distance_km"] is None

    def test_rtsp_no_thumbnail(self):
        match = {
            "ip_str": "1.2.3.4",
            "port": 554,
            "transport": "tcp",
        }
        result = _parse_match(match, 40.7, -74.0)
        assert result["thumbnail_url"] is None

    def test_screenshot_data(self):
        match = {
            "ip_str": "1.2.3.4",
            "port": 80,
            "screenshot": {
                "data": "base64data==",
                "mime": "image/png",
            },
        }
        result = _parse_match(match, 0, 0)
        assert result["screenshot_b64"] == "base64data=="
        assert result["screenshot_mime"] == "image/png"

    def test_no_screenshot(self):
        match = {"ip_str": "1.2.3.4", "port": 80}
        result = _parse_match(match, 0, 0)
        assert result["screenshot_b64"] is None


class TestShodanClientInit:
    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="SHODAN_API_KEY"):
            ShodanClient("")


@respx.mock
class TestShodanClientSearch:
    def _mock_shodan(self, matches, status=200):
        respx.get(SHODAN_SEARCH_URL).mock(
            return_value=httpx.Response(
                status,
                json={"matches": matches},
            )
        )

    def test_basic_search(self):
        self._mock_shodan(
            [
                {
                    "ip_str": "1.2.3.4",
                    "port": 80,
                    "location": {
                        "latitude": 40.7,
                        "longitude": -74.0,
                    },
                }
            ]
        )
        client = ShodanClient("test-key")
        results = client.search_cameras(
            lat=40.7, lng=-74.0, radius_miles=5
        )
        assert len(results) == 1
        assert results[0]["ip"] == "1.2.3.4"

    def test_deduplication(self):
        dup = {
            "ip_str": "1.2.3.4",
            "port": 80,
            "location": {
                "latitude": 40.7,
                "longitude": -74.0,
            },
        }
        self._mock_shodan([dup, dup, dup])
        client = ShodanClient("test-key")
        results = client.search_cameras(
            lat=40.7, lng=-74.0, radius_miles=5
        )
        assert len(results) == 1

    def test_401_raises(self):
        respx.get(SHODAN_SEARCH_URL).mock(
            return_value=httpx.Response(401, json={})
        )
        client = ShodanClient("bad-key")
        with pytest.raises(httpx.HTTPStatusError):
            client.search_cameras(
                lat=40.7, lng=-74.0, radius_miles=5
            )

    def test_403_raises(self):
        respx.get(SHODAN_SEARCH_URL).mock(
            return_value=httpx.Response(403, json={})
        )
        client = ShodanClient("free-key")
        with pytest.raises(httpx.HTTPStatusError):
            client.search_cameras(
                lat=40.7, lng=-74.0, radius_miles=5
            )

    def test_429_raises(self):
        respx.get(SHODAN_SEARCH_URL).mock(
            return_value=httpx.Response(429, json={})
        )
        client = ShodanClient("key")
        with pytest.raises(httpx.HTTPStatusError):
            client.search_cameras(
                lat=40.7, lng=-74.0, radius_miles=5
            )

    def test_500_skipped(self):
        respx.get(SHODAN_SEARCH_URL).mock(
            return_value=httpx.Response(
                500, json={"error": "bad query"}
            )
        )
        client = ShodanClient("key")
        results = client.search_cameras(
            lat=40.7, lng=-74.0, radius_miles=5
        )
        assert results == []

    def test_empty_results(self):
        self._mock_shodan([])
        client = ShodanClient("key")
        results = client.search_cameras(
            lat=40.7, lng=-74.0, radius_miles=5
        )
        assert results == []

    def test_max_results_cap(self):
        matches = [
            {
                "ip_str": f"1.2.3.{i}",
                "port": 80,
                "location": {
                    "latitude": 40.7,
                    "longitude": -74.0,
                },
            }
            for i in range(50)
        ]
        self._mock_shodan(matches)
        client = ShodanClient("key")
        results = client.search_cameras(
            lat=40.7,
            lng=-74.0,
            radius_miles=5,
            max_results=10,
        )
        assert len(results) == 10
