import os
from flask import Flask, request, render_template, send_file, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import PyPDF2
import uuid
import asyncio
import edge_tts

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp/uploads'
app.config['AUDIO_FOLDER'] = '/tmp/audio'
app.config['STATIC_FOLDER'] = 'static'

# Create directories if they don't exist
try:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['AUDIO_FOLDER'], exist_ok=True)
    os.makedirs(app.config['STATIC_FOLDER'], exist_ok=True)
    os.makedirs(os.path.join(app.config['STATIC_FOLDER'], 'icons'), exist_ok=True)
    # Test if we can write to these directories
    test_file = os.path.join(app.config['AUDIO_FOLDER'], 'test.txt')
    with open(test_file, 'w') as f:
        f.write('test')
    os.remove(test_file)
except Exception as e:
    print(f"Directory setup error: {e}")

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
        
        # Clean up the file after extraction
        os.remove(filepath)
        
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
    
    try:
        # Special handling for async operation in a serverless context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Use a timeout to prevent hanging
        task = loop.create_task(generate_speech(text, voice, audio_path))
        try:
            # Set a reasonable timeout for the operation
            loop.run_until_complete(asyncio.wait_for(task, timeout=50.0))
        except asyncio.TimeoutError:
            print("TTS operation timed out")
            return jsonify({'error': 'TTS operation timed out'}), 500
        finally:
            loop.close()
        
        # Verify the file was created
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            print(f"Audio file not created or empty: {audio_path}")
            return jsonify({'error': 'Failed to generate audio file'}), 500
            
        return jsonify({'audio_id': audio_id})
    except Exception as e:
        error_msg = str(e)
        print(f"Error in generate_audio: {error_msg}")
        return jsonify({'error': f'Error generating audio: {error_msg}'}), 500

async def generate_speech(text, voice, output_path):
    """Generate speech using Edge TTS and save to a file."""
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
    except Exception as e:
        print(f"Edge TTS failed: {e}, falling back to gTTS")
        try:
            # Fallback to gTTS
            from gtts import gTTS
            # Extract language code from voice name (e.g., 'en-US-ChristopherNeural' -> 'en')
            lang = voice.split('-')[0] if '-' in voice else 'en'
            tts = gTTS(text=text, lang=lang, slow=False)
            tts.save(output_path)
        except Exception as fallback_error:
            print(f"gTTS fallback also failed: {fallback_error}")
            raise Exception(f"Both TTS engines failed: {e} and {fallback_error}")

@app.route('/audio/<audio_id>')
def get_audio(audio_id):
    audio_path = os.path.join(app.config['AUDIO_FOLDER'], f"{audio_id}.mp3")
    if os.path.exists(audio_path):
        # Send file with auto-cleanup
        return send_file(audio_path, mimetype='audio/mp3', as_attachment=True, 
                        download_name=f"pdf_audio_{audio_id}.mp3")
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
    
    # Apply minimal cleaning to preserve original text structure
    cleaned_text = basic_text_cleanup(text)
    return cleaned_text

def basic_text_cleanup(text):
    """Apply minimal cleanup to preserve original text structure."""
    import re
    
    # Replace multiple consecutive spaces with a single space
    text = re.sub(r' {2,}', ' ', text)
    
    # Replace tabs with a single space
    text = text.replace('\t', ' ')
    
    # Replace multiple consecutive line breaks with two line breaks (paragraph separation)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Normalize all line endings
    text = re.sub(r'\r\n?', '\n', text)
    
    # Final trim
    text = text.strip()
    
    return text

# For serverless deployment
app = app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 