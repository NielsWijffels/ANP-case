import whisper
import torch
from config import WHISPER_MODEL, HF_TOKEN
import os

os.environ['HF_TOKEN'] = HF_TOKEN

# Auto-detect device - Whisper on macOS MPS has issues, use CPU
# But we can use GPU for other models
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'  # Force CPU for Whisper on macOS
FP16_AVAILABLE = DEVICE == 'cuda'

def transcribe_audio(audio_path):
    """Transcribeer audio naar Nederlands met timestamps (GPU optimized)"""
    print(f"🎤 Loading Whisper model: {WHISPER_MODEL} (device: {DEVICE})")
    model = whisper.load_model(WHISPER_MODEL, device=DEVICE)
    
    print(f"🎤 Transcribing: {audio_path}")
    result = model.transcribe(
        audio_path,
        language='nl',
        task='transcribe',
        verbose=False,
        word_timestamps=True,
        fp16=FP16_AVAILABLE,  # Enable FP16 on GPU
        temperature=0.3,  # More deterministic
        beam_size=5  # Faster than default 5
    )
    
    print(f"✓ Transcribed {len(result['segments'])} segments")
    return result
    
    print(f"✓ Transcribed {len(result['segments'])} segments")
    return result

def get_segment_text(segments, start_time=None, end_time=None):
    """Extract text from segments within time range (in seconds)"""
    text = []
    
    for seg in segments:
        seg_start = seg.get('start', 0)
        seg_end = seg.get('end', 0)
        
        if start_time and seg_end < start_time:
            continue
        if end_time and seg_start > end_time:
            continue
            
        text.append(seg['text'].strip())
    
    return ' '.join(text)

if __name__ == '__main__':
    # Test
    test_file = './temp/test.mp3'
    if os.path.exists(test_file):
        result = transcribe_audio(test_file)
        print(result['text'][:200])
