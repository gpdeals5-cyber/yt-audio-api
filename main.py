import os
import re
import time
import uuid
import logging
import threading
from pathlib import Path

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ytmp3-api")

app = Flask(__name__)
CORS(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["60 per hour", "20 per minute"]
)

DOWNLOAD_DIR = Path("/tmp/ytmp3r")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_TTL_SECONDS = 600
MAX_DURATION_SECONDS = 18000  # 5 hours limit

_tokens = {}
_tokens_lock = threading.Lock()

def _cleanup_expired_tokens():
    while True:
        time.sleep(60)
        now = time.time()
        expired = []
        with _tokens_lock:
            for token, meta in list(_tokens.items()):
                if now - meta["created_at"] > TOKEN_TTL_SECONDS:
                    expired.append(token)
                    del _tokens[token]
        for token in expired:
            for p in DOWNLOAD_DIR.glob(f"{token}.*"):
                try:
                    p.unlink(missing_ok=True)
                except Exception as e:
                    logger.error(f"Cleanup error for {p}: {e}")

threading.Thread(target=_cleanup_expired_tokens, daemon=True).start()

def _extract_video_id(url_or_id: str) -> str:
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    
    # 11-char ID match
    if re.match(r"^[A-Za-z0-9_-]{11}$", url_or_id):
        return url_or_id
        
    # Extract ID from all URL types including share ?si= parameters
    match = re.search(r"(?:v=|\/|shorts\/|youtu\.be\/)([A-Za-z0-9_-]{11})", url_or_id)
    if match:
        return match.group(1)
            
    return None

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def convert():
    raw_url = request.args.get("url")
    if not raw_url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    video_id = _extract_video_id(raw_url)
    if not video_id:
        return jsonify({"error": "Invalid YouTube URL or ID"}), 400

    full_url = f"https://www.youtube.com/watch?v={video_id}"
    token = uuid.uuid4().hex

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'outtmpl': str(DOWNLOAD_DIR / f"{token}.%(ext)s"),
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 60,
        'retries': 10,
        'fragment_retries': 10,
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'mweb', 'android', 'ios'],
                'player_skip': ['configs']
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(full_url, download=False)
            duration = info.get("duration", 0)
            if duration and duration > MAX_DURATION_SECONDS:
                return jsonify({"error": "Video duration exceeds limit"}), 400

            ydl.download([full_url])

        file_path = DOWNLOAD_DIR / f"{token}.mp3"
        if not file_path.exists():
            return jsonify({"error": "Conversion failed, file not generated"}), 500

        with _tokens_lock:
            _tokens[token] = {
                "file_path": str(file_path),
                "created_at": time.time(),
                "title": info.get("title", "audio")
            }

        download_url = f"/download?token={token}"
        return jsonify({
            "token": token,
            "download_url": download_url
        }), 200

    except Exception as e:
        logger.error(f"Error processing {full_url}: {e}")
        return jsonify({"error": "Conversion failed", "details": str(e)}), 500

@app.route("/download", methods=["GET"])
def download():
    token = request.args.get("token")
    if not token:
        abort(400, description="Missing token")

    with _tokens_lock:
        meta = _tokens.get(token)

    if not meta:
        abort(404, description="Invalid or expired token")

    file_path = Path(meta["file_path"])
    if not file_path.exists():
        abort(404, description="File not found")

    safe_title = re.sub(r'[^\w\s-]', '', meta["title"]).strip() or "audio"
    filename = f"{safe_title}.mp3"

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="audio/mpeg"
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

