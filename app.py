import os
import subprocess
import threading
import requests
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from flask import Flask, render_template, request, Response, jsonify

app = Flask(__name__)

# Thread-safe state management
stream_state = {
    'status': 'idle', 
    'message': '',
    'cancel_download': False
}
process = None
state_lock = threading.Lock()

# No fallback passwords. These must be set in Koyeb.
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')

def check_auth(username, password):
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        return False # Deny access if environment variables are not set
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    return Response('Authentication required.', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated

def format_dropbox_url(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query['dl'] = ['1']
    parsed = parsed._replace(query=urlencode(query, doseq=True))
    return urlunparse(parsed)

def run_stream_task(dropbox_url, stream_key):
    global process
    download_url = format_dropbox_url(dropbox_url)
    local_filename = "video.mp4"
    max_size = 2 * 1024 * 1024 * 1024 # 2GB limit
    
    try:
        with requests.get(download_url, stream=True, timeout=15) as r:
            r.raise_for_status()
            content_length = r.headers.get('Content-Length')
            if content_length and int(content_length) > max_size:
                raise ValueError("File exceeds the 2GB limit.")

            downloaded = 0
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    with state_lock:
                        if stream_state['cancel_download']:
                            raise InterruptedError("Cancelled")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > max_size:
                            raise ValueError("File exceeded the 2GB limit.")
    except InterruptedError:
        with state_lock:
            stream_state['status'] = 'idle'
            stream_state['message'] = 'Download cancelled manually.'
        return
    except Exception as e:
        with state_lock:
            stream_state['status'] = 'error'
            stream_state['message'] = f"Download failed: {str(e)}"
        return

    with state_lock:
        if stream_state['cancel_download']:
            stream_state['status'] = 'idle'
            stream_state['message'] = 'Stream stopped before playing.'
            return
        stream_state['status'] = 'starting'
        stream_state['message'] = 'Launching FFmpeg...'

    youtube_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    command = [
        'ffmpeg', '-re', '-stream_loop', '-1', 
        '-i', local_filename, '-c:v', 'copy', '-c:a', 'copy', 
        '-f', 'flv', youtube_url
    ]
    
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3) # Wait to ensure FFmpeg doesn't immediately crash
        if process.poll() is not None:
            raise RuntimeError("FFmpeg crashed on startup. Verify video format.")
        
        with state_lock:
            stream_state['status'] = 'streaming'
            stream_state['message'] = 'Video is live on YouTube.'
            
        process.wait() 
    except Exception as e:
        with state_lock:
            stream_state['status'] = 'error'
            stream_state['message'] = f'FFmpeg error: {str(e)}'
    finally:
        with state_lock:
            if stream_state['status'] == 'streaming':
                stream_state['status'] = 'error'
                stream_state['message'] = 'Stream ended unexpectedly.'
        process = None

@app.route('/')
@requires_auth
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    # Unauthenticated endpoint for GitHub Actions to ping safely
    return "OK", 200

@app.route('/status')
@requires_auth
def status():
    with state_lock:
        return jsonify(stream_state)

@app.route('/start', methods=['POST'])
@requires_auth
def start_stream():
    dropbox_link = request.form.get('dropbox_link', '')
    stream_key = request.form.get('stream_key', '')
    
    if not dropbox_link.startswith('https://www.dropbox.com/'):
        return jsonify({'error': 'Must be a valid https://www.dropbox.com link.'}), 400
    if not stream_key:
        return jsonify({'error': 'Missing stream key.'}), 400

    with state_lock:
        if stream_state['status'] in ['downloading', 'starting', 'streaming']:
            return jsonify({'error': 'A stream or download is already active.'}), 400
        
        # Lock in the downloading state before the thread starts
        stream_state['status'] = 'downloading'
        stream_state['message'] = 'Downloading video from Dropbox...'
        stream_state['cancel_download'] = False

    threading.Thread(target=run_stream_task, args=(dropbox_link, stream_key), daemon=True).start()
    return jsonify({'success': 'Started.'})

@app.route('/stop', methods=['POST'])
@requires_auth
def stop_stream():
    global process
    with state_lock:
        if stream_state['status'] == 'idle':
            return jsonify({'success': 'Already stopped.'})
        
        if stream_state['status'] == 'downloading':
            stream_state['cancel_download'] = True
            stream_state['message'] = 'Cancelling download...'
        else:
            if process is not None:
                process.terminate()
                process = None
            stream_state['status'] = 'idle'
            stream_state['message'] = 'Stream stopped manually.'
        
    return jsonify({'success': 'Stop initiated.'})
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
    
