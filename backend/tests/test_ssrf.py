"""Tests for SSRF protection in main.py and stream_manager.py."""

import pytest

from main import _reject_private_ip
from stream_manager import _reject_private_ip as sm_reject


class TestRejectPrivateIP:
    """Tests for main._reject_private_ip."""

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.0.1",
            "192.168.1.100",
            "0.0.0.0",
            "169.254.1.1",
            "224.0.0.1",
            "255.255.255.255",
        ],
    )
    def test_rejects_private_ipv4(self, ip):
        with pytest.raises(ValueError):
            _reject_private_ip(ip)

    def test_rejects_localhost_string(self):
        with pytest.raises(ValueError, match="Private"):
            _reject_private_ip("localhost")

    def test_rejects_dotlocal(self):
        with pytest.raises(ValueError, match="Private"):
            _reject_private_ip("myhost.local")

    def test_rejects_ipv6_loopback(self):
        with pytest.raises(ValueError, match="Private"):
            _reject_private_ip("::1")

    def test_rejects_ipv4_mapped_ipv6_loopback(self):
        with pytest.raises(ValueError):
            _reject_private_ip("::ffff:127.0.0.1")

    def test_rejects_ipv4_mapped_ipv6_private(self):
        with pytest.raises(ValueError):
            _reject_private_ip("::ffff:192.168.1.1")

    def test_rejects_hostname(self):
        with pytest.raises(
            ValueError, match="Only IP addresses"
        ):
            _reject_private_ip("evil.example.com")

    def test_accepts_public_ipv4(self):
        _reject_private_ip("8.8.8.8")

    def test_accepts_public_ipv4_2(self):
        _reject_private_ip("93.184.216.34")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            _reject_private_ip("")


class TestStreamManagerSSRF:
    """Defense-in-depth: stream_manager also rejects private IPs."""

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "0.0.0.0",
            "::ffff:127.0.0.1",
        ],
    )
    def test_rejects_private(self, ip):
        assert sm_reject(ip) is True

    def test_rejects_hostname(self):
        assert sm_reject("evil.example.com") is True

    def test_accepts_public(self):
        assert sm_reject("8.8.8.8") is False
