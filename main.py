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
import imageio_ffmpeg

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

app = Flask(__name__)
CORS(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["500 per day", "100 per hour"],
    storage_uri="memory://"
)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

FILE_TITLES = {}

def clean_filename(title):
    cleaned = re.sub(r'[\\/*?:"<>|]', "", title)
    return cleaned.strip() or "audio"

def schedule_file_deletion(token: str, delay_seconds: int = 1800):
    def delete_file():
        try:
            for f in DOWNLOAD_DIR.glob(f"{token}*"):
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

    # Fast Extraction Logic: Re-encoding bypass for ultra-fast response
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': out_template,
        'ffmpeg_location': FFMPEG_PATH,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 10,
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'mweb'],
                'player_skip': ['webpage', 'configs']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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

        schedule_file_deletion(token, 1800)
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
    
    # Deliver file smoothly with original audio response
    return send_file(
        file_path,
        as_attachment=True,
        download_name=f"{custom_title}.mp3",
        mimetype="audio/mpeg"
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
