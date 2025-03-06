# PDF to Voice Converter

A simple web application that converts PDF documents to audio. This app extracts text from PDF files and then generates speech from that text.

## Features

- Upload PDF documents and extract text
- Convert extracted text to speech
- Select from multiple languages for text-to-speech
- Download generated audio files
- Progressive Web App (PWA) capabilities for mobile and desktop
- Responsive design that works on all devices
- Offline capabilities

## Technology Stack

- **Backend**: Python with Flask
- **PDF Text Extraction**: PyPDF2
- **Text-to-Speech**: Microsoft Edge TTS (high-quality, natural-sounding voices)
- **Frontend**: HTML, CSS, JavaScript with Bootstrap 5

## Installation

1. Clone this repository:
```
git clone https://github.com/yourusername/pdftovoice.git
cd pdftovoice
```

2. Create a virtual environment (recommended):
```
python -m venv venv
```

3. Activate the virtual environment:
   - On Windows:
   ```
   venv\Scripts\activate
   ```
   - On macOS/Linux:
   ```
   source venv/bin/activate
   ```

4. Install dependencies:
```
pip install -r requirements.txt
```

## Usage

1. Start the application:
```
python app.py
```

2. Open your web browser and go to:
```
http://localhost:5000
```

3. On a mobile device, connect to your computer's IP address:
```
http://your-computer-ip:5000
```

4. Use the web interface to:
   - Upload a PDF file
   - Extract text from the PDF
   - Generate audio from the extracted text
   - Play or download the audio file
   
5. Install as a PWA (optional):
   - On desktop: Look for the install icon in your browser's address bar
   - On mobile: Use "Add to Home Screen" in your browser menu

## Mobile Usage

The app is designed to work well on mobile devices. When accessing from a mobile browser, you can install it as a home screen app by:

1. Opening the site in your mobile browser
2. Tapping the browser menu (⋮)
3. Selecting "Add to Home Screen" or "Install App"

This allows you to use the app in fullscreen mode without the browser interface.

## Limitations

- The application works best with PDFs that have properly formatted text
- Very large PDF files may take longer to process
- There is a character limit for the Google Text-to-Speech API (approximately 5000 characters per request)
- The app doesn't handle scanned PDFs that don't have searchable text

## License

MIT

## Acknowledgements

- [Flask](https://flask.palletsprojects.com/)
- [PyPDF2](https://pythonhosted.org/PyPDF2/)
- [Microsoft Edge TTS](https://github.com/rany2/edge-tts)
- [Bootstrap](https://getbootstrap.com/) 