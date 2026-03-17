"""
Google Colab Optimization Config
Auto-detects GPU and optimizes settings
"""

import os
import torch
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# AUTO-DETECT ENVIRONMENT
# ============================================================================

try:
    import google.colab
    IN_COLAB = True
    print("✓ Running in Google Colab")
except ImportError:
    IN_COLAB = False
    print("ℹ️  Running locally")

# ============================================================================
# GPU/DEVICE OPTIMIZATION
# ============================================================================

if torch.cuda.is_available():
    DEVICE = 'cuda'
    GPU_NAME = torch.cuda.get_device_name(0)
    print(f"✓ CUDA available: {GPU_NAME}")
elif torch.backends.mps.is_available():
    DEVICE = 'mps'
    print("✓ MPS (Metal) available (Mac)")
else:
    DEVICE = 'cpu'
    print("⚠️  Using CPU (slow!)")

# ============================================================================
# PERFORMANCE SETTINGS
# ============================================================================

# Whisper: Use FP16 on GPU for 2x speedup
if DEVICE == 'cuda':
    WHISPER_FP16 = True
    WHISPER_MODEL = 'tiny'  # Smallest + fastest
    CHUNK_DURATION = 5  # Process in 5-min chunks
else:
    WHISPER_FP16 = False
    WHISPER_MODEL = 'tiny'
    CHUNK_DURATION = 5

# Diarization batch size (higher = faster but more RAM)
if IN_COLAB and DEVICE == 'cuda':
    DIARIZATION_BATCH_SIZE = 32  # Colab has good RAM
else:
    DIARIZATION_BATCH_SIZE = 16

# ============================================================================
# API & TOKENS
# ============================================================================

HF_TOKEN = os.getenv('HF_TOKEN')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'mistral')
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
NEWS_SCORE_THRESHOLD = float(os.getenv('NEWS_SCORE_THRESHOLD', 0.65))

YOUTUBE_CHANNEL = '@gemeenteranst1107'

# ============================================================================
# DIRECTORIES
# ============================================================================

if IN_COLAB:
    OUTPUT_DIR = '/content/output'
    TEMP_DIR = '/content/temp'
else:
    OUTPUT_DIR = './output'
    TEMP_DIR = './temp'

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# ============================================================================
# PROCESSING SETTINGS
# ============================================================================

LOOKBACK_DURATION = 30  # Minutes for news context
PARALLEL_CHUNKS = 2  # Extract 2 chunks in parallel
STREAMING_MODE = True  # Live alerts vs batch
