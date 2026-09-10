import os
import secrets
import threading
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

def schedule_file_deletion(filepath: Path, delay_seconds: int = 600):
    def delete_file():
        try:
            if filepath.exists():
                filepath.unlink()
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
        # Bot verification bypass settings
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
            ydl.download([url])

        file_path = DOWNLOAD_DIR / f"{token}.mp3"
        
        if not file_path.exists():
            generated_files = list(DOWNLOAD_DIR.glob(f"{token}.*"))
            if generated_files:
                file_path = generated_files[0]
            else:
                return jsonify({'error': 'Conversion failed or file not generated'}), 500

        schedule_file_deletion(file_path, 600)
        return jsonify({'download_url': f"/download?token={token}", 'token': token})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['GET'])
@app.route('/download/<token>', methods=['GET'])
def download(token=None):
    if not token:
        token = request.args.get('token')
        
    if not token:
        return jsonify({'error': 'Token parameter missing'}), 400

    generated_files = list(DOWNLOAD_DIR.glob(f"{token}.*"))
    if not generated_files:
        return jsonify({'error': 'Link expired or file not found'}), 404

    file_path = generated_files[0]
    return send_file(
        file_path,
        as_attachment=True,
        download_name=f"audio{file_path.suffix}",
        mimetype="audio/mpeg"
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
