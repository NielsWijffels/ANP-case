# 🚀 RANST POC - Colab Streaming Edition

**Live news alerts for gemeente council meetings**

## ⚡ Quick Start (Colab)

### 1️⃣ Clone & Setup
```python
!git clone https://github.com/yourusername/ranst-poc.git
%cd ranst-poc
!pip install -q -r requirements.txt
```

### 2️⃣ Configure
```python
# Set your HuggingFace token
import os
os.environ['HF_TOKEN'] = 'your_token_here'

# Update .env
with open('.env', 'w') as f:
    f.write(f"HF_TOKEN={os.environ['HF_TOKEN']}\n")
    f.write("OLLAMA_MODEL=mistral\n")
    f.write("OLLAMA_BASE_URL=http://localhost:11434\n")
```

### 3️⃣ Start Ollama
```bash
!bash /content/start_ollama.sh &
```

Wait 30 seconds, then verify:
```python
import requests
import time
time.sleep(30)
try:
    r = requests.get('http://localhost:11434/api/tags')
    print("✓ Ollama ready!")
except:
    print("❌ Ollama not ready yet")
```

### 4️⃣ Run Streaming Analysis
```bash
!python streaming_main.py /path/to/video.mp4
```

## 📊 What happens

```
Phase 1: OCR intro (30 sec)
├─ Extract speaker names from first 5 minutes
└─ Create speaker map

Phase 2: Streaming chunks (5 min each)
├─ Extract chunk (parallel)
├─ Transcribe (tiny model + FP16 GPU = 2x speed)
├─ Diarize
├─ Detect news
├─ 🚨 Alert ANP if newsworthy
└─ Repeat...

Phase 3: Finalize
└─ Save complete results + all emails
```

## 📧 Output

Files created in `/content/output/`:
- `ranst_news_TIMESTAMP.json` - Full results
- `ranst_news_TIMESTAMP_live.json` - Live updates
- `ranst_news_TIMESTAMP_email_alert_001.txt` - Mock emails
- `ranst_news_TIMESTAMP_email_alert_002.txt`
- ...

## ⏱️ Speed (Colab GPU)

| Phase | Expected |
|-------|----------|
| OCR intro | 30 sec |
| Per 5-min chunk | 2-3 min |
| **First alert** | **~6-7 min** |
| **Per chunk after** | **~5 min** |

For 100-min video: Total ~2-2.5 hours with live alerts!

## 🤝 ANP Integration

Mock emails are saved as text files. In production:
```python
import requests

# Send real email via API
for alert_file in glob.glob('output/*_email_alert_*.txt'):
    with open(alert_file) as f:
        content = f.read()
    
    requests.post('https://your-anp-api.com/alerts', 
        json={'content': content})
```

## 🔧 Troubleshooting

### "OCR not available"
```bash
!pip install -q easyocr opencv-python
```

### "Ollama not ready"
- Wait longer (30-60 seconds)
- Check: `curl http://localhost:11434/api/tags`

### "Out of memory"
- Reduce `WHISPER_MODEL` to 'tiny'
- Reduce chunk duration to 3 min

### "GPU not detected"
- Restart runtime: Runtime → Restart runtime
- Check: `!nvidia-smi` (should show GPU)

---

**🎯 For Production**: Deploy on dedicated server with persistent GPU + message queue (RabbitMQ) for 100% uptime during council meetings.
