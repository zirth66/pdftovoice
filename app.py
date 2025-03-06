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
import logging
import traceback
import sys
import threading
from pydub import AudioSegment

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('pdftovoice')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp/uploads'
app.config['AUDIO_FOLDER'] = '/tmp/audio'
app.config['STATIC_FOLDER'] = 'static'
app.config['DEBUG'] = True  # Enable debug mode

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
    logger.info("Directory setup completed successfully")
except Exception as e:
    logger.error(f"Directory setup error: {e}")

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

# Status tracking for audio generation jobs
# Keys are audio_ids, values are {'status': 'processing'|'completed'|'failed', 'error': error_message}
JOB_STATUS = {}

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
        logger.warning("No file part in request")
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        logger.warning("No selected file")
        return jsonify({'error': 'No selected file'}), 400
    
    if file and file.filename.lower().endswith('.pdf'):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        logger.info(f"Saved file: {filepath}")
        
        try:
            # Extract text from PDF
            logger.info(f"Extracting text from PDF: {filepath}")
            extracted_text = extract_text_from_pdf(filepath)
            if not extracted_text:
                logger.warning(f"Failed to extract text from PDF: {filepath}")
                return jsonify({'error': 'Failed to extract text from PDF. The file might be encrypted or contain only images.'}), 400
            
            # Clean up - delete the uploaded file
            os.remove(filepath)
            logger.info(f"Deleted file after processing: {filepath}")
            
            return jsonify({'text': extracted_text})
        except Exception as e:
            logger.error(f"Error processing PDF: {str(e)}", exc_info=True)
            # Clean up in case of error
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted file after error: {filepath}")
            return jsonify({'error': f'Error processing PDF: {str(e)}'}), 500
    else:
        logger.warning(f"Unsupported file type: {file.filename}")
        return jsonify({'error': 'Unsupported file type. Please upload a PDF file.'}), 400

@app.route('/generate-audio', methods=['POST'])
def generate_audio():
    try:
        data = request.get_json()
        logger.info(f"Audio generation request received: {len(data.get('text', ''))} characters")
        
        if not data or 'text' not in data:
            logger.warning("No text provided in request")
            return jsonify({'error': 'No text provided'}), 400
        
        text = data['text']
        if not text.strip():
            logger.warning("Empty text provided")
            return jsonify({'error': 'Empty text provided'}), 400
            
        lang_code = data.get('voice', 'en')  # Default to English
        
        # Get the appropriate voice for the language
        voice = VOICE_MAPPING.get(lang_code, 'en-US-ChristopherNeural')
        logger.info(f"Using voice: {voice} for language: {lang_code}")
        
        # Generate unique ID for the audio file
        audio_id = str(uuid.uuid4())
        audio_filename = f"{audio_id}.mp3"
        audio_path = os.path.join(app.config['AUDIO_FOLDER'], audio_filename)
        logger.info(f"Audio will be saved to: {audio_path}")
        
        # Initialize status tracking
        JOB_STATUS[audio_id] = {'status': 'processing', 'error': None}
        
        # Start the TTS processing in a background thread
        # This allows us to return immediately while processing continues
        thread = threading.Thread(
            target=process_tts_in_background,
            args=(text, voice, audio_path, audio_id)
        )
        thread.daemon = True  # Daemon thread will be terminated when main thread exits
        thread.start()
        
        # Return immediately with the audio_id
        # Client will poll for status
        logger.info(f"Started background TTS processing for {audio_id}")
        return jsonify({
            'audio_id': audio_id, 
            'status': 'processing',
            'message': 'Audio generation started in background'
        })
            
    except Exception as e:
        # Catch-all for any unexpected errors
        error_msg = str(e)
        logger.error(f"Unexpected error in generate_audio endpoint: {error_msg}", exc_info=True)
        return jsonify({
            'error': f'Unexpected error: {error_msg}', 
            'traceback': traceback.format_exc()
        }), 500

def process_tts_in_background(text, voice, audio_path, audio_id):
    """Process TTS in a background thread to avoid Vercel timeouts"""
    try:
        logger.info(f"Background processing started for {audio_id}")
        start_time = time.time()
        
        # Create a placeholder file to indicate processing is happening
        with open(audio_path + '.processing', 'w') as f:
            f.write('Processing')
        
        # Set up a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the async TTS processing
        result = loop.run_until_complete(process_text_in_chunks(text, voice, audio_path))
        
        # Check result and update status
        if result and os.path.exists(audio_path) and os.path.getsize(audio_path) > 100:
            JOB_STATUS[audio_id]['status'] = 'completed'
            duration = time.time() - start_time
            logger.info(f"Background processing completed successfully for {audio_id} in {duration:.2f} seconds")
        else:
            JOB_STATUS[audio_id]['status'] = 'failed'
            JOB_STATUS[audio_id]['error'] = 'Failed to generate audio file (file missing or empty)'
            logger.error(f"Background processing failed for {audio_id}: audio file not created properly")
        
        # Clean up the processing indicator file
        try:
            if os.path.exists(audio_path + '.processing'):
                os.remove(audio_path + '.processing')
        except Exception as e:
            logger.error(f"Error removing processing indicator file: {str(e)}")
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in background TTS processing for {audio_id}: {error_msg}", exc_info=True)
        JOB_STATUS[audio_id]['status'] = 'failed'
        JOB_STATUS[audio_id]['error'] = f"Error generating audio: {error_msg}"
        
        # Create an error file to indicate failure
        try:
            with open(audio_path + '.error', 'w') as f:
                f.write(f"Error: {error_msg}\n\n{traceback.format_exc()}")
        except Exception:
            pass

@app.route('/audio-status/<audio_id>', methods=['GET'])
def get_audio_status(audio_id):
    """Check the status of an audio generation job"""
    if audio_id in JOB_STATUS:
        status_info = JOB_STATUS[audio_id]
        return jsonify(status_info)
    
    # If not in JOB_STATUS, check if file exists (might have been generated in a previous run)
    audio_path = os.path.join(app.config['AUDIO_FOLDER'], f"{audio_id}.mp3")
    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 100:
        return jsonify({'status': 'completed', 'error': None})
    
    # Check for error file
    if os.path.exists(audio_path + '.error'):
        try:
            with open(audio_path + '.error', 'r') as f:
                error_msg = f.read()
            return jsonify({'status': 'failed', 'error': error_msg})
        except Exception:
            pass
    
    # Check for processing file
    if os.path.exists(audio_path + '.processing'):
        return jsonify({'status': 'processing', 'error': None})
    
    # If we can't determine status, assume it doesn't exist
    return jsonify({'status': 'not_found', 'error': 'Audio job not found'}), 404

async def process_text_in_chunks(text, voice, output_path):
    """Process text in chunks to avoid timeouts."""
    # Settings for chunking
    chunk_size = 300  # characters per chunk - smaller for faster processing
    max_chunks = 20   # limit total processing time
    
    # Split text into sentences to make natural chunks
    logger.info(f"Splitting text into chunks (text length: {len(text)})")
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
        logger.warning(f"Text has {len(chunks)} chunks, limiting to {max_chunks}")
        chunks = chunks[:max_chunks]
        chunks.append("... Text was truncated due to length limitations.")
        
    logger.info(f"Processing text in {len(chunks)} chunks")
    
    # Process each chunk with a smaller timeout
    chunk_timeout = 8  # seconds per chunk - reduced for faster processing
    temp_files = []
    
    for i, chunk in enumerate(chunks):
        chunk_start = time.time()
        logger.info(f"Processing chunk {i+1}/{len(chunks)}, length: {len(chunk)}")
        
        # Create temporary file for this chunk
        temp_path = f"{output_path}.chunk_{i}.mp3"
        temp_files.append(temp_path)
        
        try:
            # Use asyncio.wait_for to enforce timeout for each chunk
            logger.debug(f"Starting Edge TTS for chunk {i+1}")
            await asyncio.wait_for(
                _generate_speech(chunk, voice, temp_path),
                timeout=chunk_timeout
            )
            chunk_duration = time.time() - chunk_start
            logger.info(f"Chunk {i+1} processed in {chunk_duration:.2f} seconds")
            
            # Verify chunk was created
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                logger.debug(f"Chunk {i+1} audio file created successfully: {temp_path}, size: {os.path.getsize(temp_path)}")
            else:
                logger.error(f"Chunk {i+1} audio file missing or empty: {temp_path}")
                
        except asyncio.TimeoutError:
            logger.error(f"Timeout processing chunk {i+1}")
            # Continue with other chunks
            continue
        except Exception as e:
            logger.error(f"Error processing chunk {i+1}: {str(e)}", exc_info=True)
            # Continue with other chunks
            continue
    
    # Combine all chunk files into one mp3
    logger.info(f"Combining {len(temp_files)} audio chunks")
    combined = _combine_audio_files(temp_files, output_path)
    
    # Clean up temporary files
    for temp_file in temp_files:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
                logger.debug(f"Removed temporary file: {temp_file}")
            except Exception as e:
                logger.error(f"Error removing temporary file {temp_file}: {str(e)}")
    
    return combined

def _combine_audio_files(file_paths, output_path):
    """Combine multiple MP3 files into one."""
    if not file_paths:
        logger.error("No files to combine")
        return False
        
    # Filter out non-existent files
    existing_files = [f for f in file_paths if os.path.exists(f) and os.path.getsize(f) > 0]
    logger.info(f"Found {len(existing_files)} valid audio files out of {len(file_paths)} total files")
    
    if not existing_files:
        logger.error("No valid audio files to combine")
        return False
        
    # If only one file, just rename it
    if len(existing_files) == 1:
        logger.info(f"Only one valid file, copying to output: {existing_files[0]} -> {output_path}")
        # Copy the single file to the output path
        try:
            with open(existing_files[0], 'rb') as src, open(output_path, 'wb') as dst:
                dst.write(src.read())
            return True
        except Exception as e:
            logger.error(f"Error copying single file: {str(e)}", exc_info=True)
            return False
    
    try:
        # Combine audio files using pydub
        logger.info(f"Combining {len(existing_files)} audio files")
        combined = AudioSegment.empty()
        for file_path in existing_files:
            try:
                chunk = AudioSegment.from_mp3(file_path)
                combined += chunk
                logger.debug(f"Added file to combined audio: {file_path}, duration: {len(chunk)/1000}s")
            except Exception as e:
                logger.error(f"Error processing audio file {file_path}: {str(e)}")
                # Continue with other files
                continue
            
        combined.export(output_path, format="mp3")
        logger.info(f"Combined audio exported to {output_path}, duration: {len(combined)/1000}s")
        return True
    except Exception as e:
        logger.error(f"Error combining audio files: {str(e)}", exc_info=True)
        # If combining fails, use the first file as fallback
        try:
            logger.info(f"Attempting fallback - using first file: {existing_files[0]}")
            with open(existing_files[0], 'rb') as src, open(output_path, 'wb') as dst:
                dst.write(src.read())
            return True
        except Exception as fallback_error:
            logger.error(f"Fallback failed: {str(fallback_error)}")
            return False

async def _generate_speech(text, voice, output_path):
    """Internal function to generate speech using Edge TTS."""
    try:
        logger.debug(f"Creating Edge TTS communicate object for voice: {voice}")
        communicate = edge_tts.Communicate(text, voice)
        
        # Use a more direct approach - write to memory first
        audio_data = io.BytesIO()
        logger.debug("Starting Edge TTS stream")
        
        chunk_count = 0
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.write(chunk["data"])
                chunk_count += 1
        
        logger.debug(f"Received {chunk_count} audio chunks from Edge TTS")
        
        # Write the complete audio to disk
        audio_data.seek(0)
        audio_size = len(audio_data.getvalue())
        logger.debug(f"Writing {audio_size} bytes to {output_path}")
        
        with open(output_path, 'wb') as f:
            f.write(audio_data.read())
            
        # Verify the file was written correctly
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            logger.debug(f"File written successfully: {output_path}, size: {file_size} bytes")
            if file_size == 0:
                logger.error(f"Empty file created: {output_path}")
                raise Exception("Generated audio file is empty")
        else:
            logger.error(f"File not created: {output_path}")
            raise Exception("Failed to create audio file")
            
    except Exception as e:
        logger.error(f"Error in _generate_speech: {str(e)}", exc_info=True)
        raise

@app.route('/audio/<audio_id>')
def get_audio(audio_id):
    audio_path = os.path.join(app.config['AUDIO_FOLDER'], f"{audio_id}.mp3")
    logger.info(f"Audio request for: {audio_id}")
    
    if os.path.exists(audio_path):
        logger.info(f"Serving audio file: {audio_path}, size: {os.path.getsize(audio_path)}")
        # Send file with auto-cleanup
        return send_file(audio_path, mimetype='audio/mp3', as_attachment=True, 
                        download_name=f"pdf_audio_{audio_id}.mp3")
    else:
        # Check status to provide more helpful error
        if audio_id in JOB_STATUS:
            status = JOB_STATUS[audio_id]['status']
            if status == 'processing':
                logger.info(f"Audio file {audio_id} is still processing")
                return jsonify({'error': 'Audio file is still being generated'}), 202
            elif status == 'failed':
                error = JOB_STATUS[audio_id]['error'] or 'Unknown error'
                logger.error(f"Audio file {audio_id} generation failed: {error}")
                return jsonify({'error': f'Audio generation failed: {error}'}), 500
        
        logger.error(f"Audio file not found: {audio_path}")
        return jsonify({'error': 'Audio file not found'}), 404

def extract_text_from_pdf(pdf_path):
    logger.info(f"Extracting text from PDF: {pdf_path}")
    text = ""
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            num_pages = len(reader.pages)
            logger.info(f"PDF has {num_pages} pages")
            
            for page_num in range(num_pages):
                logger.debug(f"Processing page {page_num+1}/{num_pages}")
                page = reader.pages[page_num]
                page_text = page.extract_text()
                text += page_text + "\n"
                logger.debug(f"Page {page_num+1} extracted {len(page_text)} characters")
        
        # Apply minimal cleaning to preserve original text structure
        cleaned_text = basic_text_cleanup(text)
        logger.info(f"Extraction complete. Original text: {len(text)} chars, Cleaned text: {len(cleaned_text)} chars")
        return cleaned_text
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {str(e)}", exc_info=True)
        raise

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

# Error handler for all 500 errors
@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {str(e)}", exc_info=True)
    return jsonify({
        'error': 'Server error occurred',
        'details': str(e),
        'traceback': traceback.format_exc()
    }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 