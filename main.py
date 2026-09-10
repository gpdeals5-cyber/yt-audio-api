import os
import secrets
import threading
import re
from pathlib import Path
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

app = Flask(__name__)
CORS(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

FILE_TITLES = {}

def clean_filename(title):
    cleaned = re.sub(r'[\\/*?:"<>|]', "", title)
    return cleaned.strip()

def schedule_file_deletion(token: str, delay_seconds: int = 600):
    def delete_file():
        try:
            generated_files = list(DOWNLOAD_DIR.glob(f"{token}*"))
            for f in generated_files:
                if f.exists():
                    f.unlink()
            FILE_TITLES.pop(token, None)
        except Exception:
            pass
    threading.Timer(delay_seconds, delete_file).start()

@app.route('/', methods=['GET'])
def index():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'Missing url parameter'}), 400

    token = secrets.token_hex(16)
    out_template = str(DOWNLOAD_DIR / f"{token}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': out_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 10,
        'noplaylist': True,
        'prefer_ffmpeg': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'android', 'ios'],
                'player_skip': ['webpage', 'configs']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
        }
    }

    if os.path.exists('cookies.txt'):
        ydl_opts['cookiefile'] = 'cookies.txt'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_title = info.get('title', 'audio') if info else 'audio'
            FILE_TITLES[token] = clean_filename(video_title)

        generated_files = list(DOWNLOAD_DIR.glob(f"{token}*"))
        if not generated_files:
            return jsonify({'error': 'Conversion failed or file not generated'}), 500

        schedule_file_deletion(token, 600)
        return jsonify({
            'download_url': f"/download?token={token}",
            'token': token,
            'title': FILE_TITLES[token]
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['GET'])
@app.route('/download/<token>', methods=['GET'])
def download(token=None):
    if not token:
        token = request.args.get('token')
        
    if not token:
        return jsonify({'error': 'Token parameter missing'}), 400

    generated_files = list(DOWNLOAD_DIR.glob(f"{token}*"))
    if not generated_files:
        return jsonify({'error': 'Link expired or file not found'}), 404

    file_path = generated_files[0]
    custom_title = FILE_TITLES.get(token, "audio")
    download_filename = f"{custom_title}.mp3"

    return send_file(
        file_path,
        as_attachment=True,
        download_name=download_filename,
        mimetype="audio/mpeg"
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
