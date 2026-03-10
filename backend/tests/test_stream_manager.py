"""Tests for stream_manager module."""

from unittest.mock import MagicMock, patch

import pytest

from stream_manager import (
    MAX_CONCURRENT_STREAMS,
    MAX_STREAMS_PER_IP,
    _lock,
    _streams,
    get_stream_file,
    start_stream,
    stop_stream,
)


@pytest.fixture(autouse=True)
def _clear_streams():
    """Ensure streams are clean between tests."""
    yield
    with _lock:
        _streams.clear()


class TestStartStream:
    @patch("stream_manager._probe_url", return_value=False)
    def test_no_reachable_url(self, mock_probe):
        result = start_stream("8.8.8.8", 554, "_generic")
        assert result is None

    def test_rejects_private_ip(self):
        result = start_stream("192.168.1.1", 554, "_generic")
        assert result is None

    def test_rejects_loopback(self):
        result = start_stream("127.0.0.1", 554, "_generic")
        assert result is None

    @patch("stream_manager._wait_for_segment", return_value=True)
    @patch("stream_manager.subprocess.Popen")
    @patch("stream_manager._probe_url", return_value=True)
    def test_success(self, mock_probe, mock_popen, mock_wait):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = start_stream("8.8.8.8", 554, "_generic")
        assert result is not None
        assert len(result) == 32  # uuid4 hex

        # cleanup
        stop_stream(result)

    @patch("stream_manager._wait_for_segment", return_value=True)
    @patch("stream_manager.subprocess.Popen")
    @patch("stream_manager._probe_url", return_value=True)
    def test_concurrent_cap(
        self, mock_probe, mock_popen, mock_wait
    ):
        mock_popen.return_value = MagicMock()

        ids = []
        for i in range(MAX_CONCURRENT_STREAMS):
            sid = start_stream(
                f"8.8.{i // 256}.{i % 256}", 554, "_generic"
            )
            assert sid is not None
            ids.append(sid)

        # Next one should be rejected
        result = start_stream("9.9.9.9", 554, "_generic")
        assert result is None

        for sid in ids:
            stop_stream(sid)

    @patch("stream_manager._wait_for_segment", return_value=True)
    @patch("stream_manager.subprocess.Popen")
    @patch("stream_manager._probe_url", return_value=True)
    def test_per_ip_cap(
        self, mock_probe, mock_popen, mock_wait
    ):
        mock_popen.return_value = MagicMock()

        ids = []
        for i in range(MAX_STREAMS_PER_IP):
            sid = start_stream(
                f"8.8.8.{i}", 554, "_generic",
                requester_ip="10.0.0.1",
            )
            assert sid is not None
            ids.append(sid)

        result = start_stream(
            "8.8.8.100", 554, "_generic",
            requester_ip="10.0.0.1",
        )
        assert result is None

        for sid in ids:
            stop_stream(sid)

    @patch("stream_manager._probe_url", return_value=True)
    @patch("stream_manager._wait_for_segment", return_value=False)
    @patch("stream_manager.subprocess.Popen")
    def test_no_segments_produced(
        self, mock_popen, mock_wait, mock_probe
    ):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = start_stream("8.8.8.8", 554, "_generic")
        assert result is None
        mock_proc.kill.assert_called_once()


class TestStopStream:
    def test_stop_nonexistent(self):
        stop_stream("nonexistent")  # should not raise

    @patch("stream_manager._wait_for_segment", return_value=True)
    @patch("stream_manager.subprocess.Popen")
    @patch("stream_manager._probe_url", return_value=True)
    def test_stop_cleans_up(
        self, mock_probe, mock_popen, mock_wait
    ):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        sid = start_stream("8.8.8.8", 554, "_generic")
        assert sid is not None

        stop_stream(sid)
        mock_proc.terminate.assert_called_once()

        assert sid not in _streams


class TestGetStreamFile:
    def test_unknown_stream(self):
        assert get_stream_file("nonexistent", "foo.ts") is None

    @patch("stream_manager._wait_for_segment", return_value=True)
    @patch("stream_manager.subprocess.Popen")
    @patch("stream_manager._probe_url", return_value=True)
    def test_missing_file(
        self, mock_probe, mock_popen, mock_wait
    ):
        mock_popen.return_value = MagicMock()

        sid = start_stream("8.8.8.8", 554, "_generic")
        assert sid is not None

        result = get_stream_file(sid, "nonexistent.ts")
        assert result is None

        stop_stream(sid)
