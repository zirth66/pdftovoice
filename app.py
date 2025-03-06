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
    """Process text for speech synthesis.
    
    Note: This function name is preserved for compatibility, but it now processes
    the entire text at once instead of in chunks.
    """
    logger.info(f"Processing text for audio generation, length: {len(text)} characters")
    
    # Maximum text length to process (to avoid excessive processing time)
    max_length = 20000
    
    # Check if text is too long and truncate if necessary
    if len(text) > max_length:
        logger.warning(f"Text is too long ({len(text)} chars), truncating to {max_length} chars")
        # Try to truncate at a sentence boundary
        truncated_text = text[:max_length]
        last_period = truncated_text.rfind('.')
        if last_period > max_length * 0.8:  # If we can find a period in the last 20% of the text
            truncated_text = truncated_text[:last_period+1]
        text = truncated_text + "\n\n[Text was truncated due to length limitations.]"
    
    # Try with retries for the entire text
    max_retries = 2
    success = False
    
    for retry in range(max_retries + 1):
        if retry > 0:
            logger.info(f"Retry {retry}/{max_retries} for full text generation")
        
        try:
            # Generate speech for the entire text at once
            success = await generate_speech_full(text, voice, output_path)
            if success:
                logger.info(f"Successfully generated audio at {output_path}")
                break
            else:
                logger.error(f"Failed to generate audio on attempt {retry+1}/{max_retries+1}")
        except Exception as e:
            logger.error(f"Error in text processing, retry {retry}/{max_retries}: {str(e)}", exc_info=True)
    
    if not success:
        logger.error("All attempts to generate audio failed")
        return False
    
    return output_path

async def _generate_speech(text, voice, output_path):
    """Internal function to generate speech using Edge TTS.
    
    Note: Kept for backward compatibility, now delegates to generate_speech_full.
    """
    try:
        logger.debug(f"Generating speech using _generate_speech (delegating to generate_speech_full)")
        result = await generate_speech_full(text, voice, output_path)
        if result:
            return True
        else:
            raise Exception("Speech generation failed")
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

async def generate_speech_full(text, voice, output_path):
    """Generate speech for the entire text directly without chunking.
    
    Args:
        text (str): The text to convert to speech
        voice (str): The voice to use
        output_path (str): Path where the audio file should be saved
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        logger.info(f"Generating speech for text of length {len(text)} using Edge TTS directly")
        
        # Create communicate object with voice and rate settings
        communicate = edge_tts.Communicate(text, voice, rate="+25%")
        
        # Open the output file directly
        with open(output_path, "wb") as file:
            audio_chunk_count = 0
            total_bytes = 0
            
            # Set longer timeout for full text processing
            timeout_seconds = max(30, min(300, len(text) // 50))
            logger.debug(f"Using timeout of {timeout_seconds} seconds based on text length")
            
            try:
                async with asyncio.timeout(timeout_seconds):
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            file.write(chunk["data"])
                            total_bytes += len(chunk["data"])
                            audio_chunk_count += 1
                            
                            # Log progress for large texts
                            if audio_chunk_count % 50 == 0:
                                logger.debug(f"Processed {audio_chunk_count} audio chunks, {total_bytes} bytes")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout after processing {audio_chunk_count} chunks. Using partial results.")
                if audio_chunk_count == 0:
                    raise Exception("TTS processing timed out without generating any audio")
        
        # Verify the output file
        if not os.path.exists(output_path):
            logger.error("Output file was not created")
            return False
            
        file_size = os.path.getsize(output_path)
        logger.info(f"Generated audio file: {output_path}, size: {file_size} bytes")
        
        if file_size == 0:
            logger.error("Generated audio file is empty")
            return False
            
        return True
        
    except Exception as e:
        logger.error(f"Error in generate_speech_full: {str(e)}", exc_info=True)
        return False

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 