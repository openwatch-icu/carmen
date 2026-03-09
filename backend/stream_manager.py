"""
RTSP → HLS transcoding manager.

Spawns one ffmpeg subprocess per camera stream, writing HLS segments to
a temporary directory. Flask serves those files directly. Idle streams
are cleaned up automatically after STREAM_TIMEOUT_S seconds.
"""

import atexit
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

log = logging.getLogger(__name__)

STREAM_TIMEOUT_S = 90  # kill stream if not accessed for N seconds
SEGMENT_READY_TIMEOUT = 8  # seconds to wait for first HLS segment
SEGMENT_POLL_INTERVAL = 0.2
MAX_CONCURRENT_STREAMS = int(os.environ.get("MAX_CONCURRENT_STREAMS", "10"))
MAX_STREAMS_PER_IP = int(os.environ.get("MAX_STREAMS_PER_IP", "3"))

# Common RTSP stream paths per brand, tried in order.
RTSP_PATHS: dict[str, list[str]] = {
    "Hikvision": [
        "/Streaming/Channels/101",
        "/Streaming/Channels/1",
        "/h264/ch1/main/av_stream",
    ],
    "Dahua": [
        "/cam/realmonitor?channel=1&subtype=0",
        "/cam/realmonitor?channel=1&subtype=1",
    ],
    "Axis": [
        "/axis-media/media.amp",
        "/mpeg4/media.amp",
    ],
    "Foscam": [
        "/videoMain",
        "/11",
    ],
    "_generic": [
        "/",
        "/live",
        "/stream",
        "/live/ch0",
        "/live/main",
        "/live/0/main",
        "/live.sdp",
        "/h264",
        "/video",
        "/stream1",
        "/av0_0",
        "/ch01.264",
    ],
}

# Active streams: stream_id → metadata dict
_streams: dict[str, dict] = {}
_lock = threading.Lock()


def _candidate_urls(ip: str, port: int, brand: str) -> list[str]:
    paths = RTSP_PATHS.get(brand, []) + RTSP_PATHS["_generic"]
    return [f"rtsp://{ip}:{port}{p}" for p in paths]


def _ffmpeg_cmd(rtsp_url: str, playlist: str, seg_pattern: str) -> list[str]:
    return [
        "ffmpeg",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-an",  # drop audio — reduces latency
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "4",
        "-hls_flags",
        "delete_segments+append_list",
        "-hls_segment_filename",
        seg_pattern,
        "-y",
        playlist,
    ]


def _wait_for_segment(tmpdir: str) -> bool:
    """Block until an .ts segment exists or timeout expires."""
    deadline = time.monotonic() + SEGMENT_READY_TIMEOUT
    while time.monotonic() < deadline:
        if any(f.endswith(".ts") for f in os.listdir(tmpdir)):
            return True
        time.sleep(SEGMENT_POLL_INTERVAL)
    return False


def _probe_url(rtsp_url: str) -> bool:
    """
    Return True if ffprobe can open the RTSP URL within 3 seconds.
    Used to pick the correct path before committing an ffmpeg process.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-rtsp_transport",
                "tcp",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                rtsp_url,
            ],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0 and b"video" in result.stdout
    except subprocess.TimeoutExpired, FileNotFoundError:
        return False


def start_stream(
    ip: str, port: int, brand: str, requester_ip: str = ""
) -> str | None:
    """
    Start an ffmpeg HLS transcoding process for a camera.

    Probes candidate RTSP URLs and starts ffmpeg on the first that
    responds. Returns a stream_id string, or None if no URL responded.
    """
    with _lock:
        if len(_streams) >= MAX_CONCURRENT_STREAMS:
            log.warning("Stream cap reached (%d)", MAX_CONCURRENT_STREAMS)
            return None
        if (
            requester_ip
            and sum(
                1
                for s in _streams.values()
                if s.get("requester_ip") == requester_ip
            )
            >= MAX_STREAMS_PER_IP
        ):
            log.warning(
                "Per-IP stream cap reached (%d) for %s",
                MAX_STREAMS_PER_IP,
                requester_ip,
            )
            return None

    candidates = _candidate_urls(ip, port, brand)

    rtsp_url: str | None = None
    for url in candidates:
        log.info("Probing %s", url)
        if _probe_url(url):
            rtsp_url = url
            log.info("Probe success: %s", url)
            break

    if rtsp_url is None:
        log.warning(
            "No reachable RTSP URL found for %s:%d (%s)",
            ip,
            port,
            brand,
        )
        return None

    tmpdir = tempfile.mkdtemp(prefix="openwatch_")
    playlist = os.path.join(tmpdir, "playlist.m3u8")
    seg_pattern = os.path.join(tmpdir, "seg%03d.ts")

    cmd = _ffmpeg_cmd(rtsp_url, playlist, seg_pattern)
    log.info("Starting ffmpeg: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_for_segment(tmpdir):
        log.warning("ffmpeg produced no segments for %s", rtsp_url)
        proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None

    stream_id = uuid.uuid4().hex
    with _lock:
        _streams[stream_id] = {
            "process": proc,
            "tmpdir": tmpdir,
            "last_access": time.monotonic(),
            "rtsp_url": rtsp_url,
            "requester_ip": requester_ip,
        }

    log.info("Stream %s ready (%s)", stream_id, rtsp_url)
    return stream_id


def get_stream_file(stream_id: str, filename: str) -> bytes | None:
    """
    Return the raw bytes of an HLS file for the given stream, or None.
    Updates last_access timestamp for idle-timeout tracking.
    """
    with _lock:
        stream = _streams.get(stream_id)
        if stream is None:
            return None
        stream["last_access"] = time.monotonic()
        path = os.path.join(stream["tmpdir"], filename)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def stop_stream(stream_id: str) -> None:
    """Kill the ffmpeg process and remove temp files."""
    with _lock:
        stream = _streams.pop(stream_id, None)
    if stream is None:
        return

    proc: subprocess.Popen = stream["process"]
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        proc.kill()

    shutil.rmtree(stream["tmpdir"], ignore_errors=True)
    log.info("Stopped stream %s", stream_id)


def _cleanup_loop() -> None:
    """Background thread: stop streams idle longer than STREAM_TIMEOUT_S."""
    while True:
        time.sleep(15)
        now = time.monotonic()
        with _lock:
            idle = [
                sid
                for sid, s in _streams.items()
                if now - s["last_access"] > STREAM_TIMEOUT_S
            ]
        for sid in idle:
            log.info("Idle timeout — stopping stream %s", sid)
            stop_stream(sid)


# Start cleanup thread when module is imported
_cleanup_thread = threading.Thread(
    target=_cleanup_loop, daemon=True, name="stream-cleanup"
)
_cleanup_thread.start()


def _shutdown_all_streams() -> None:
    """Stop all active streams on process exit."""
    with _lock:
        ids = list(_streams.keys())
    for sid in ids:
        stop_stream(sid)


atexit.register(_shutdown_all_streams)
