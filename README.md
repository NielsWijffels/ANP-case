# 🎙️ RANST - Automatische Raadsvergadering Nieuwsdetectie

Gemeenteraad video → Transcriptie → Sprekerherkenning → Nieuwsdetectie → ANP Alerts

## 🚀 Snel starten (1 klik)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/YOUR_USERNAME/ranst-poc/blob/main/RANST_Colab_Streaming.ipynb)

> **Vervang `YOUR_USERNAME` hierboven met je GitHub username na het aanmaken van de repo.**

### Hoe te gebruiken:
1. Klik op de **"Open in Colab"** badge hierboven
2. Selecteer GPU: `Runtime > Change runtime type > T4 GPU`
3. Klik `Runtime > Run all`
4. Wacht tot de resultaten automatisch downloaden als zip
5. Klaar! De runtime sluit automatisch af om GPU vrij te geven

### Wat het doet:
- Haalt automatisch de laatste video op van het Ranst gemeenteraad YouTube kanaal
- Geen handmatige input nodig
- Progress updates elke 5 minuten met percentage en geschatte tijd
- Resultaten: JSON + leesbaar transcript + email alerts

---

## Lokaal draaien (optioneel)

```bash
pip install -r requirements.txt
python main.py
```

This will:
1. 🎥 Find latest Ranst YouTube stream
2. ⬇️ Download audio
3. 🎤 Transcribe (Whisper)
4. 🎙️ Identify speakers (pyannote)
5. 🔍 Detect newsworthy segments (Ollama LLM)
6. 💾 Save JSON with alerts + full transcript

## Output

Results go to `./output/ranst_news_*.json`:
- News alerts (score, category, reason)
- Full transcript with speaker identification
- Timestamps for each segment

## Pipeline Flow

```
YouTube Stream
    ↓
yt-dlp (download)
    ↓
Whisper (transcribe to Dutch)
    ↓
pyannote (speaker diarization)
    ↓
Combine segments with speakers
    ↓
Every 5 min: extract last 30 min of transcript
    ↓
Ollama LLM: score for newsworthiness (0-1)
    ↓
If score > 0.65: alert!
    ↓
Save JSON output
```

## Ollama Models (pick one)

```bash
ollama pull mistral      # Fast, good for Dutch
ollama pull llama2       # Slower, very good quality  
ollama pull neural-chat  # Balanced
```

## Next Steps

1. Add webhook to send alerts to ANP
2. Add database to store alerts + transcripts
3. Add query API for searching old meetings
4. Scale to other municipalities
5. Add email/Slack notifications
