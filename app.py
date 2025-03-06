import os
from flask import Flask, request, render_template, send_file, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import PyPDF2
import uuid
import asyncio
import edge_tts
import time
import io
import re
from pydub import AudioSegment

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
    
    if file and file.filename.lower().endswith('.pdf'):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            # Extract text from PDF
            extracted_text = extract_text_from_pdf(filepath)
            if not extracted_text:
                return jsonify({'error': 'Failed to extract text from PDF. The file might be encrypted or contain only images.'}), 400
            
            # Clean up - delete the uploaded file
            os.remove(filepath)
            
            return jsonify({'text': extracted_text})
        except Exception as e:
            # Clean up in case of error
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': f'Error processing PDF: {str(e)}'}), 500
    else:
        return jsonify({'error': 'Unsupported file type. Please upload a PDF file.'}), 400

@app.route('/generate-audio', methods=['POST'])
def generate_audio():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text']
    if not text.strip():
        return jsonify({'error': 'Empty text provided'}), 400
        
    lang_code = data.get('voice', 'en')  # Default to English
    
    # Get the appropriate voice for the language
    voice = VOICE_MAPPING.get(lang_code, 'en-US-ChristopherNeural')
    
    # Generate unique ID for the audio file
    audio_id = str(uuid.uuid4())
    audio_filename = f"{audio_id}.mp3"
    audio_path = os.path.join(app.config['AUDIO_FOLDER'], audio_filename)
    
    # Start time for logging
    start_time = time.time()
    print(f"Starting audio generation with Edge TTS: {voice}, text length: {len(text)}")
    
    try:
        # Create a new event loop for this request
        asyncio.set_event_loop(asyncio.new_event_loop())
        
        # Process the text in chunks to avoid timeouts
        result = asyncio.run(process_text_in_chunks(text, voice, audio_path))
        
        # Check if audio was generated
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 100:  # Ensure file isn't empty
            print(f"Audio file not created properly: {audio_path}")
            return jsonify({'error': 'Failed to generate audio file (file missing or empty)'}), 500
            
        duration = time.time() - start_time
        print(f"Audio generation completed in {duration:.2f} seconds")
        return jsonify({'audio_id': audio_id})
        
    except asyncio.TimeoutError:
        print("TTS operation timed out")
        return jsonify({'error': 'TTS operation timed out - please try with shorter text'}), 500
    except Exception as e:
        error_msg = str(e)
        print(f"Error in generate_audio: {error_msg}")
        return jsonify({'error': f'Error generating audio: {error_msg}'}), 500

async def process_text_in_chunks(text, voice, output_path):
    """Process text in chunks to avoid timeouts."""
    # Settings for chunking
    chunk_size = 500  # characters per chunk (adjust as needed)
    max_chunks = 20   # limit total processing time
    
    # Split text into sentences to make natural chunks
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""
    
    # Create chunks of text by combining sentences until reaching chunk_size
    for sentence in sentences:
        if len(current_chunk) + len(sentence) <= chunk_size:
            current_chunk += " " + sentence if current_chunk else sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence
    
    # Add the last chunk if not empty
    if current_chunk:
        chunks.append(current_chunk)
    
    # Limit number of chunks for processing time
    if len(chunks) > max_chunks:
        print(f"Warning: Text has {len(chunks)} chunks, limiting to {max_chunks}")
        chunks = chunks[:max_chunks]
        chunks.append("... Text was truncated due to length limitations.")
        
    print(f"Processing text in {len(chunks)} chunks")
    
    # Process each chunk with a smaller timeout
    chunk_timeout = 10  # seconds per chunk
    temp_files = []
    
    for i, chunk in enumerate(chunks):
        chunk_start = time.time()
        print(f"Processing chunk {i+1}/{len(chunks)}, length: {len(chunk)}")
        
        # Create temporary file for this chunk
        temp_path = f"{output_path}.chunk_{i}.mp3"
        temp_files.append(temp_path)
        
        try:
            # Use asyncio.wait_for to enforce timeout for each chunk
            await asyncio.wait_for(
                _generate_speech(chunk, voice, temp_path),
                timeout=chunk_timeout
            )
            chunk_duration = time.time() - chunk_start
            print(f"Chunk {i+1} processed in {chunk_duration:.2f} seconds")
        except asyncio.TimeoutError:
            print(f"Timeout processing chunk {i+1}")
            # Continue with other chunks
            continue
        except Exception as e:
            print(f"Error processing chunk {i+1}: {str(e)}")
            # Continue with other chunks
            continue
    
    # Combine all chunk files into one mp3
    combined = _combine_audio_files(temp_files, output_path)
    
    # Clean up temporary files
    for temp_file in temp_files:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
    
    return combined

def _combine_audio_files(file_paths, output_path):
    """Combine multiple MP3 files into one."""
    if not file_paths:
        return False
        
    # Filter out non-existent files
    existing_files = [f for f in file_paths if os.path.exists(f) and os.path.getsize(f) > 0]
    if not existing_files:
        return False
        
    # If only one file, just rename it
    if len(existing_files) == 1:
        # Copy the single file to the output path
        with open(existing_files[0], 'rb') as src, open(output_path, 'wb') as dst:
            dst.write(src.read())
        return True
    
    try:
        # Combine audio files using pydub
        combined = AudioSegment.empty()
        for file_path in existing_files:
            chunk = AudioSegment.from_mp3(file_path)
            combined += chunk
            
        combined.export(output_path, format="mp3")
        return True
    except Exception as e:
        print(f"Error combining audio files: {str(e)}")
        # If combining fails, use the first file as fallback
        try:
            with open(existing_files[0], 'rb') as src, open(output_path, 'wb') as dst:
                dst.write(src.read())
            return True
        except:
            return False

async def _generate_speech(text, voice, output_path):
    """Internal function to generate speech using Edge TTS."""
    communicate = edge_tts.Communicate(text, voice)
    
    # Use a more direct approach - write to memory first
    audio_data = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.write(chunk["data"])
    
    # Write the complete audio to disk
    audio_data.seek(0)
    with open(output_path, 'wb') as f:
        f.write(audio_data.read())

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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 