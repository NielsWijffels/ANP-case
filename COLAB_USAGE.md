# 🚀 RANST POC - Google Colab Setup

Run de RANST news detection in Google Colab! Volg deze stappen:

## Stap 1: Clone de repo in Colab

```python
!git clone https://github.com/yourusername/ranst-poc.git
%cd ranst-poc
```

## Stap 2: Setup en install dependencies

```python
!python colab_setup.py
```

Dit installeert:
- ✅ FFmpeg
- ✅ Python packages (Whisper, pyannote, torch)
- ✅ Ollama (local LLM)

## Stap 3: Voeg HuggingFace token toe

LET OP: Je hebt een HuggingFace token nodig van https://huggingface.co/settings/tokens

```python
# Option A: Edit .env file
with open('.env', 'r') as f:
    print(f.read())

# Edit the HF_TOKEN line, then save
```

Of direct instellen in Colab:

```python
import os
os.environ['HF_TOKEN'] = 'your_token_here'

# Also update .env
with open('.env', 'w') as f:
    f.write(f"""HF_TOKEN={os.environ['HF_TOKEN']}
OLLAMA_MODEL=mistral
OLLAMA_BASE_URL=http://localhost:11434
NEWS_SCORE_THRESHOLD=0.65
""")
```

## Stap 4: Start Ollama service

```bash
!bash /content/start_ollama.sh &
```

Let op: Wacht ~30 seconden tot Ollama klaar is!

Controleer of Ollama draait:
```python
import time
time.sleep(30)  # Wacht 30 seconden

import requests
try:
    r = requests.get('http://localhost:11434/api/tags', timeout=5)
    print("✅ Ollama is running!")
except:
    print("❌ Ollama is nog niet klaar, wacht nog even...")
```

## Stap 5: Run de analyse

### Option A: Met YouTube URL
```python
!python main.py
```

Dit zal automatisch de nieuwste Ranst video downloaden en analyseren.

### Option B: Met lokaal audiobestand
```python
# Upload je audio naar Colab eerst, of plaats het pad
!python main.py /content/je_audio_file.mp3
```

## Stap 6: Bekijk de resultaten

```python
import json
import os

# List output files
output_files = os.listdir('/content/output')
print("Output files:", output_files)

# Read latest result
if output_files:
    latest = sorted(output_files)[-1]
    with open(f'/content/output/{latest}', 'r') as f:
        data = json.load(f)
    
    print("\n🚨 NEWS ALERTS:")
    for alert in data['news_alerts']:
        print(f"  - {alert['chunk_time']}: {alert['reason']} (score: {alert['score']})")
    
    print(f"\n📝 Total segments: {len(data['full_transcript'])}")
```

## 💡 Tips & Tricks

### Memory issues?
Gebruik een kleinere Whisper model:
```python
# In .env:
WHISPER_MODEL=tiny  # or base, small
```

### Meer controle over verwerking?
Edit config.py:
```python
CHUNK_DURATION = 10  # Analyse elke 10 minuten
LOOKBACK_DURATION = 20  # Kijk terug naar 20 minuten
NEWS_SCORE_THRESHOLD = 0.7  # Hogere threshold = minder alerts
```

### Ollama down?
Restart in Colab:
```python
import subprocess
subprocess.run("pkill -f 'ollama serve'", shell=True)
import time
time.sleep(2)
!bash /content/start_ollama.sh &
```

## ⚠️ Limitations in Colab

- **Ollama Models**: Download pakt ~5GB (+Whisper model)
- **Runtime**: Free Colab kan tot 12 uur draien
- **Storage**: Free Colab heeft 100GB beschikbaar
- **GPU**: Gratis GPU beschikbaar (sneller verwerking)

## 🐛 Troubleshooting

### "ModuleNotFoundError: No module named 'X'"
```python
!pip install -q package_name
```

### "Ollama connection refused"
```python
# Olammा is niet gestart, run:
!bash /content/start_ollama.sh &
```

### "No stream found"
Gebruik een lokaal audiobestand:
```python
!python main.py /path/to/audio.mp3
```

---

**Need help?** Check `/content/output/` for detailed results 🎙️
