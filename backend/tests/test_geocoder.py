"""Tests for geocoder module."""

import httpx
import pytest
import respx

from geocoder import NOMINATIM_URL, geocode_location


@respx.mock
class TestGeocodeLocation:
    def test_happy_path(self):
        respx.get(NOMINATIM_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "40.7128",
                        "lon": "-74.0060",
                        "display_name": "New York, NY, USA",
                    }
                ],
            )
        )
        result = geocode_location("New York")
        assert result is not None
        assert result["lat"] == pytest.approx(40.7128)
        assert result["lng"] == pytest.approx(-74.006)
        assert "New York" in result["display_name"]

    def test_empty_results(self):
        respx.get(NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=[])
        )
        result = geocode_location("xyznonexistent")
        assert result is None

    def test_http_error(self):
        respx.get(NOMINATIM_URL).mock(
            return_value=httpx.Response(500)
        )
        result = geocode_location("New York")
        assert result is None

    def test_invalid_json(self):
        respx.get(NOMINATIM_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"not json",
                headers={
                    "content-type": "text/plain",
                },
            )
        )
        result = geocode_location("New York")
        assert result is None
