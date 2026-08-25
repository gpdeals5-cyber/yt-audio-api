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

# Token Cleanup Logic (Deletes downloaded files after 10 minutes)
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
    out_path_template = str(DOWNLOAD_DIR / f"{token}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'outtmpl': out_path_template,
        'cookiefile': 'www.youtube.com_cookies.txt',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 60,
        'retries': 15,
        'noplaylist': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        file_path = DOWNLOAD_DIR / f"{token}.mp3"
        if not file_path.exists():
            return jsonify({'error': 'Conversion failed or file not generated'}), 500

        schedule_file_deletion(file_path, 600)
        return jsonify({'download_url': f"/download/{token}", 'token': token})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<token>', methods=['GET'])
def download(token):
    file_path = DOWNLOAD_DIR / f"{token}.mp3"
    if not file_path.exists():
        return jsonify({'error': 'Link expired or file not found'}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name="audio.mp3",
        mimetype="audio/mpeg"
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
