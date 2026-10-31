import os
import subprocess
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
stream_process = None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_stream():
    global stream_process
    if stream_process:
        stream_process.terminate()
        
    video_url = request.form.get('video_url')
    stream_key = request.form.get('stream_key')
    
    response = requests.get(video_url, stream=True)
    with open("local_video.mp4", 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            
    command = [
        'ffmpeg', '-stream_loop', '-1', '-re', '-i', 'local_video.mp4',
        '-c:v', 'libx264', '-preset', 'veryfast', '-b:v', '2500k',
        '-maxrate', '2500k', '-bufsize', '5000k', '-pix_fmt', 'yuv420p',
        '-g', '60', '-c:a', 'aac', '-b:a', '128k', '-ar', '44100',
        '-f', 'flv', f'rtmp://a.rtmp.youtube.com/live2/{stream_key}'
    ]
    
    stream_process = subprocess.Popen(command)
    return jsonify({"status": "Success", "message": "Stream has started locally on the server!"})

@app.route('/stop', methods=['POST'])
def stop_stream():
    global stream_process
    if stream_process:
        stream_process.terminate()
        stream_process = None
        return jsonify({"status": "Success", "message": "Stream stopped."})
    return jsonify({"status": "Error", "message": "No active stream."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
