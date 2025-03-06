import os
from flask import Flask, request, render_template, send_file, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import PyPDF2
import uuid
import asyncio
import edge_tts

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['AUDIO_FOLDER'] = 'audio'
app.config['STATIC_FOLDER'] = 'static'

# Create directories if they don't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['AUDIO_FOLDER'], exist_ok=True)
os.makedirs(app.config['STATIC_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['STATIC_FOLDER'], 'icons'), exist_ok=True)

# Map of language codes to Edge TTS voice names
VOICE_MAPPING = {
    'en': 'en-US-ChristopherNeural',
    'sv': 'sv-SE-MattiasNeural',
    'fr': 'fr-FR-HenriNeural',
    'es': 'es-ES-AlvaroNeural',
    'de': 'de-DE-ConradNeural',
    'it': 'it-IT-DiegoNeural',
    'pt': 'pt-BR-FabioNeural',
    'ru': 'ru-RU-DmitryNeural',
    'ja': 'ja-JP-KeitaNeural',
    'ko': 'ko-KR-InJoonNeural',
    'zh-CN': 'zh-CN-YunxiNeural'
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(app.config['STATIC_FOLDER'], 'service-worker.js')

@app.route('/manifest.json')
def manifest():
    return send_from_directory(app.config['STATIC_FOLDER'], 'manifest.json')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@app.route('/extract', methods=['POST'])
def extract_text():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and file.filename.endswith('.pdf'):
        # Save the uploaded PDF
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Extract text from PDF
        text = extract_text_from_pdf(filepath)
        
        # Return extracted text
        return jsonify({'text': text})
    
    return jsonify({'error': 'Invalid file format. Please upload a PDF file.'}), 400

@app.route('/generate-audio', methods=['POST'])
def generate_audio():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text']
    lang_code = data.get('voice', 'en')  # Default to English
    
    # Get the appropriate voice for the language
    voice = VOICE_MAPPING.get(lang_code, 'en-US-ChristopherNeural')
    
    # Generate unique ID for the audio file
    audio_id = str(uuid.uuid4())
    audio_filename = f"{audio_id}.mp3"
    audio_path = os.path.join(app.config['AUDIO_FOLDER'], audio_filename)
    
    # Run the async TTS in a synchronous context
    asyncio.run(generate_speech(text, voice, audio_path))
    
    return jsonify({'audio_id': audio_id})

async def generate_speech(text, voice, output_path):
    """Generate speech using Edge TTS and save to a file."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

@app.route('/audio/<audio_id>')
def get_audio(audio_id):
    audio_path = os.path.join(app.config['AUDIO_FOLDER'], f"{audio_id}.mp3")
    if os.path.exists(audio_path):
        return send_file(audio_path, mimetype='audio/mp3')
    else:
        return jsonify({'error': 'Audio file not found'}), 404

def extract_text_from_pdf(pdf_path):
    text = ""
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        num_pages = len(reader.pages)
        
        for page_num in range(num_pages):
            page = reader.pages[page_num]
            text += page.extract_text() + "\n"
    
    return text

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 