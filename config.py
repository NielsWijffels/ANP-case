import os
from dotenv import load_dotenv

load_dotenv()

# Check if running in Google Colab
try:
    import google.colab
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

HF_TOKEN = os.getenv('HF_TOKEN')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'mistral')
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
NEWS_SCORE_THRESHOLD = float(os.getenv('NEWS_SCORE_THRESHOLD', 0.65))

YOUTUBE_CHANNEL = '@gemeenteranst1107'

# Use Colab directories if in Colab
if IN_COLAB:
    OUTPUT_DIR = '/content/output'
    TEMP_DIR = '/content/temp'
else:
    OUTPUT_DIR = './output'
    TEMP_DIR = './temp'

# Audio/processing - STREAMING OPTIMIZED
WHISPER_MODEL = 'tiny'  # Fastest model
CHUNK_DURATION = 5  # Process in 5-min chunks (for streaming)
LOOKBACK_DURATION = 30  # Context for news detection
STREAMING_MODE = True  # Enable live alerts

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
