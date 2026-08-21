import os
import re
import time
import uuid
import shutil
import logging
import threading
from pathlib import Path

from flask import Flask, request, jsonify, send_file, abort, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ytmp3-api")

app = Flask(__name__)

# -----------------------------------------------------------------------
# CORS: only your own WordPress site is allowed to call this API from a
# browser. Set ALLOWED_ORIGIN(S) as an env var on Railway, e.g.:
#   ALLOWED_ORIGINS=https://www.yoursite.com,https://yoursite.com
# Multiple origins can be comma-separated (useful for www / non-www, or a
# staging domain). Do NOT use "*" here — that's what let outside sites and
# bots hit this API directly and burn your Railway resources.
# -----------------------------------------------------------------------
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
]

if not ALLOWED_ORIGINS:
    log.warning(
        "ALLOWED_ORIGINS is not set — no browser origins will be allowed. "
        "Set it in Railway's environment variables."
    )

CORS(
    app,
    resources={
        r"/*": {
            "origins": ALLOWED_ORIGINS,
            "methods": ["GET", "OPTIONS"],
        }
    },
)

# -----------------------------------------------------------------------
# Rate limiting: caps how many conversion requests a single IP can make.
# This is a first line of defense against bots/scripts hammering the API
# and exhausting CPU/bandwidth on Railway. Uses in-memory storage by
# default (fine for a single instance); point REDIS_URL at a Railway
# Redis addon if you ever run multiple instances, so limits are shared.
# -----------------------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per hour", "20 per minute"],
    storage_uri=os.environ.get("REDIS_URL", "memory://"),
)

# -----------------------------------------------------------------------
# Extra layer: reject requests that don't present one of our allowed
# Origin/Referer headers, even for non-browser clients (curl/bots that
# spoof or omit CORS-relevant headers entirely). This is defense in depth
# on top of flask-cors, which only governs what browsers enforce.
# -----------------------------------------------------------------------
def _origin_allowed():
    if not ALLOWED_ORIGINS:
        return False

    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")

    for allowed in ALLOWED_ORIGINS:
        if origin.startswith(allowed) or referer.startswith(allowed):
            return True

    return False


@app.before_request
def block_unknown_origins():
    if request.method == "OPTIONS":
        return  # let CORS preflight through

    if request.path in ("/health",):
        return

    if not _origin_allowed():
        log.info(
            "Blocked request from disallowed origin. path=%s origin=%s referer=%s ip=%s",
            request.path,
            request.headers.get("Origin"),
            request.headers.get("Referer"),
            get_remote_address(),
        )
        abort(403, description="This API only accepts requests from the configured website.")


# -----------------------------------------------------------------------
# Storage for converted files. Tokens are random UUIDs (not sequential /
# guessable) and files are deleted after TOKEN_TTL_SECONDS.
# -----------------------------------------------------------------------
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/tmp/ytmp3r"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "600"))  # 10 min
MAX_DURATION_SECONDS = int(os.environ.get("MAX_DURATION_SECONDS", "1800"))  # 30 min cap

_tokens = {}  # token -> {"path": Path, "expires": float}
_tokens_lock = threading.Lock()

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_URL_RE = re.compile(
    r"^https?://(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|embed/|live/)|youtu\.be/)[A-Za-z0-9_-]{11}"
)


def _validate_youtube_url(raw_url: str) -> str:
    """
    Strictly validate that the input is a well-formed YouTube URL and
    return a normalized watch URL. Rejects anything that isn't YouTube
    (prevents this API being used as an open proxy for arbitrary yt-dlp
    downloads, and prevents shell/argument injection into yt-dlp).
    """
    raw_url = (raw_url or "").strip()

    if not raw_url or len(raw_url) > 2048:
        abort(400, description="Missing or invalid url parameter.")

    if not YOUTUBE_URL_RE.match(raw_url):
        abort(400, description="Only youtube.com / youtu.be URLs are supported.")

    # Extract the 11-char video ID ourselves rather than trusting the
    # rest of the URL (query strings, tracking params, etc.) verbatim.
    match = re.search(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})", raw_url)
    if not match or not YOUTUBE_ID_RE.match(match.group(1)):
        abort(400, description="Could not extract a valid YouTube video ID.")

    video_id = match.group(1)
    return f"https://www.youtube.com/watch?v={video_id}"


def _cleanup_expired():
    now = time.time()
    with _tokens_lock:
        expired = [t for t, meta in _tokens.items() if meta["expires"] < now]
        for t in expired:
            meta = _tokens.pop(t)
            try:
                if meta["path"].exists():
                    meta["path"].unlink()
            except OSError:
                pass


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/")
@limiter.limit("6 per minute")  # conversion is expensive; tighter limit here
def convert():
    _cleanup_expired()

    raw_url = request.args.get("url", "")
    watch_url = _validate_youtube_url(raw_url)

    job_id = uuid.uuid4().hex
    out_template = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration <= {MAX_DURATION_SECONDS}"
        ),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([watch_url])
    except yt_dlp.utils.DownloadError as e:
        log.warning("yt-dlp failed for %s: %s", watch_url, e)
        return jsonify({
            "error": "This video could not be converted.",
            "detail": "It may be private, age-restricted, region-locked, or too long.",
        }), 422
    except Exception:
        log.exception("Unexpected conversion error for %s", watch_url)
        return jsonify({"error": "Conversion failed unexpectedly."}), 500

    mp3_path = DOWNLOAD_DIR / f"{job_id}.mp3"
    if not mp3_path.exists():
        return jsonify({"error": "Conversion finished but no output file was produced."}), 500

    token = uuid.uuid4().hex
    with _tokens_lock:
        _tokens[token] = {
            "path": mp3_path,
            "expires": time.time() + TOKEN_TTL_SECONDS,
        }

    return jsonify({"token": token})


@app.route("/download")
@limiter.limit("30 per minute")
def download():
    _cleanup_expired()

    token = request.args.get("token", "")
    if not token or not re.match(r"^[a-f0-9]{32}$", token):
        abort(400, description="Missing or invalid token.")

    with _tokens_lock:
        meta = _tokens.get(token)

    if not meta or not meta["path"].exists():
        abort(404, description="This download link has expired. Please convert again.")

    return send_file(
        meta["path"],
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name="audio.mp3",
    )


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": "Too many requests. Please slow down and try again in a minute.",
    }), 429


@app.errorhandler(403)
def forbidden_handler(e):
    return jsonify({"error": "Forbidden."}), 403


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
