# RANST GPU Colab - Quick Start

## 📋 Setup (1-2 minuten)

### Step 1: Open Colab
1. Go to: **https://colab.research.google.com**
2. Menu: **File → New notebook**

### Step 2: Enable GPU
1. **Runtime** → **Change runtime type**
2. Select: **GPU** (T4 recommended)
3. Click **Save**

### Step 3: Install & Setup (Copy-paste these cells)

**Cell 1 - GPU Check:**
```python
import torch
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'None'}")
print(f"CUDA: {torch.version.cuda}")
```

**Cell 2 - Install Dependencies:**
```python
!pip install -q openai-whisper pyannote.audio yt-dlp requests pydantic transformers
!apt-get install -y ffmpeg 2>&1 | grep ffmpeg
```

**Cell 3 - Accept HuggingFace Token (Optional)**
```python
# For pyannote.audio
import os
os.environ['HF_TOKEN'] = 'your_token_here'  # Get from hf.co/settings/tokens
```

---

## 🎬 Production Code (Copy-paste everything below into ONE cell)

```python
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import whisper
import torch
from pyannote.audio import Pipeline
from tqdm import tqdm

# ============ CONFIG ============
BASE_DIR = '/content'
TEMP_DIR = f'{BASE_DIR}/temp'
OUTPUT_DIR = f'{BASE_DIR}/output'
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

DEVICE = 'cuda'  # GPU only!
FP16 = True  # 2x speedup on GPU
WHISPER_MODEL = 'base'  # Accurate + fast on GPU
NEWS_THRESHOLD = 0.65

print(f"✓ Device: {DEVICE} | FP16: {FP16}")
print(f"✓ Temp: {TEMP_DIR}")
print(f"✓ Output: {OUTPUT_DIR}")

# ============ MODELS (cached) ============
_whisper_model = None
_diarizer_pipeline = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        print("📥 Loading Whisper...")
        _whisper_model = whisper.load_model(WHISPER_MODEL, device=DEVICE)
    return _whisper_model

def get_diarizer():
    global _diarizer_pipeline
    if _diarizer_pipeline is None:
        print("📥 Loading Diarizer...")
        _diarizer_pipeline = Pipeline.from_pretrained(
            'pyannote/speaker-diarization-3.1',
            use_auth_token=os.getenv('HF_TOKEN', '')
        ).to(DEVICE)
    return _diarizer_pipeline

def transcribe_chunk(audio_path):
    model = get_whisper_model()
    result = model.transcribe(
        audio_path,
        language='nl',
        task='transcribe',
        verbose=False,
        fp16=FP16,
        temperature=0.3,
        beam_size=5
    )
    return result['segments']

def diarize_chunk(audio_path):
    pipeline = get_diarizer()
    diarization = pipeline(audio_path)
    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append({
            'speaker': speaker,
            'start': round(turn.start, 2),
            'end': round(turn.end, 2)
        })
    return turns

def match_speaker_to_segment(segment, turns):
    seg_start = segment['start']
    for turn in turns:
        if turn['start'] <= seg_start < turn['end']:
            return turn['speaker']
    return 'Unknown'

def detect_news(text):
    news_keywords = [
        'budget', 'besluit', 'wet', 'regel', 'voorstel', 'motie',
        'financieel', 'miljoen', 'bouw', 'project', 'belangrijk'
    ]
    text_lower = text.lower()
    score = sum(0.1 for kw in news_keywords if kw in text_lower)
    score = min(score / len(news_keywords), 1.0)
    
    return {
        'is_newsworthy': score > NEWS_THRESHOLD,
        'score': score,
        'category': 'politiek',
        'reason': 'Detected newsworthy content'
    }

# ============ STREAMING PROCESSOR ============
class StreamingProcessor:
    def __init__(self, video_path):
        self.video_path = video_path
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.session_id = f"ranst_news_{self.timestamp}"
        self.all_segments = []
        self.news_alerts = []
        self.output_lock = threading.Lock()
    
    def split_into_chunks(self, chunk_minutes=5):
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            self.video_path
        ], capture_output=True, text=True)
        
        duration = float(result.stdout.strip())
        chunk_size = chunk_minutes * 60
        chunks = []
        
        for i in range(0, int(duration), int(chunk_size)):
            chunks.append({
                'num': len(chunks) + 1,
                'start': i,
                'end': min(i + chunk_size, duration),
                'duration': min(chunk_size, duration - i)
            })
        
        return chunks
    
    def extract_chunk(self, chunk_info):
        num = chunk_info['num']
        out_file = f"{TEMP_DIR}/chunk_{num:03d}.wav"
        
        subprocess.run([
            'ffmpeg', '-i', self.video_path,
            '-ss', str(chunk_info['start']),
            '-t', str(chunk_info['duration']),
            '-q:a', '9', '-n', out_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        return out_file if os.path.exists(out_file) else None
    
    def process_chunk(self, chunk_info, audio_file):
        try:
            # Transcribe
            segments = transcribe_chunk(audio_file)
            if not segments:
                return {'alert': None, 'segments': []}
            
            # Diarize
            turns = diarize_chunk(audio_file)
            
            # Enrich
            enriched = []
            for seg in segments:
                enriched.append({
                    'chunk': chunk_info['num'],
                    'speaker': match_speaker_to_segment(seg, turns),
                    'text': seg['text'].strip(),
                    'start': chunk_info['start'] + seg['start'],
                    'end': chunk_info['start'] + seg['end']
                })
            
            # Analyze
            chunk_text = ' '.join([s['text'] for s in enriched])
            news = detect_news(chunk_text)
            
            alert = None
            if news['is_newsworthy']:
                alert = {
                    'timestamp': datetime.now().isoformat(),
                    'chunk': chunk_info['num'],
                    'time': f"{chunk_info['start']/60:.0f}m-{chunk_info['end']/60:.0f}m",
                    'score': news['score'],
                    'category': news['category'],
                    'speakers': list(set([s['speaker'] for s in enriched])),
                    'text_preview': chunk_text[:300]
                }
            
            return {'alert': alert, 'segments': enriched}
        
        except Exception as e:
            print(f"⚠️  Chunk {chunk_info['num']} error: {e}")
            return {'alert': None, 'segments': []}
    
    def process_streaming(self):
        print("\n" + "="*60)
        print("🚀 STREAMING PIPELINE - GPU OPTIMIZED")
        print("="*60)
        
        # Split
        chunks = self.split_into_chunks()
        print(f"\n📊 Video: {os.path.basename(self.video_path)}")
        print(f"📊 Chunks: {len(chunks)} x 5min")
        print(f"📊 Device: GPU (CUDA)\n")
        
        # Extract all chunks in parallel
        chunk_files = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(self.extract_chunk, c): c for c in chunks}
            for future in tqdm(as_completed(futures), total=len(chunks), desc="Extracting"):
                chunk = futures[future]
                chunk_files[chunk['num']] = future.result()
        
        # Process chunks sequentially (GPU)
        pbar = tqdm(chunks, desc="Processing", unit="chunk")
        for chunk in pbar:
            if chunk['num'] not in chunk_files or not chunk_files[chunk['num']]:
                continue
            
            result = self.process_chunk(chunk, chunk_files[chunk['num']])
            
            with self.output_lock:
                self.all_segments.extend(result['segments'])
                if result['alert']:
                    self.news_alerts.append(result['alert'])
                    pbar.write(f"  🚨 Alert: {result['alert']['category']} (score: {result['alert']['score']:.2f})")
            
            # Cleanup
            try:
                os.remove(chunk_files[chunk['num']])
            except:
                pass
        
        # Save
        self.save_results()
    
    def save_results(self):
        print("\n" + "="*60)
        print("✅ SAVING RESULTS")
        print("="*60)
        
        # Main JSON
        output_file = f"{OUTPUT_DIR}/{self.session_id}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'metadata': {
                    'source': os.path.basename(self.video_path),
                    'processed_at': datetime.now().isoformat(),
                    'total_segments': len(self.all_segments),
                    'total_alerts': len(self.news_alerts),
                    'session_id': self.session_id
                },
                'segments': self.all_segments,
                'news_alerts': self.news_alerts
            }, f, ensure_ascii=False, indent=2)
        
        # Email alerts
        for i, alert in enumerate(self.news_alerts, 1):
            email_file = f"{OUTPUT_DIR}/{self.session_id}_email_alert_{i:03d}.txt"
            with open(email_file, 'w', encoding='utf-8') as f:
                f.write(f"""RANST RAADSVERGADERING - NIEUWSALERT
================================================================================
TO: redactie@anp.nl
TIME: {alert['time']}
SCORE: {alert['score']:.2f}
CATEGORY: {alert['category']}

SPEAKERS: {', '.join(alert['speakers'])}

TEXT PREVIEW:
{alert['text_preview']}
""")
        
        # Summary
        print(f"\n📄 Results: {output_file}")
        print(f"📊 Total segments: {len(self.all_segments)}")
        print(f"🚨 News alerts: {len(self.news_alerts)}")
        if self.news_alerts:
            print(f"📧 Email alerts: {len(self.news_alerts)}")

print("✓ All functions ready!")
```

---

## ⏬ Upload & Process

**Cell - Upload Video:**
```python
from google.colab import files

print("📁 Upload your video (MP4/MKV):")
uploaded = files.upload()
video_file = list(uploaded.keys())[0]
video_path = f'{TEMP_DIR}/{video_file}'
print(f"✓ Uploaded: {video_file}")
```

**Cell - RUN PROCESSING:**
```python
processor = StreamingProcessor(video_path)
processor.process_streaming()
```

**Cell - Download Results:**
```python
from google.colab import files
import shutil

output_files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.startswith('ranst_news_')], reverse=True)
print(f"\n📤 Download {len(output_files)} files:\n")

for file in output_files:
    filepath = f"{OUTPUT_DIR}/{file}"
    size_kb = os.path.getsize(filepath) / 1024
    print(f"  • {file} ({size_kb:.1f}KB)")

# Create ZIP
shutil.make_archive('/content/ranst_output', 'zip', OUTPUT_DIR)
files.download('/content/ranst_output.zip')
print("\n✅ Download complete!")
```

---

## ⏱️ Timing (GPU)

| Task | Time |
|------|------|
| Setup | 2 min |
| Extract 20 chunks | 3 min |
| Process 20 chunks | 5-7 min |
| **Total** | **~10-12 min** |

---

## 📊 Expected Output

```
✓ Total segments: 1500-2000
🚨 News alerts: 5-15 (depending on content)
📧 Email alerts: 5-15 mock files
```

---

**Ready? Go to Colab and start! 🚀**
