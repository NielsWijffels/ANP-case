#!/usr/bin/env python3
"""
RANST GEMEENTE - Real-time News Detection POC
Transcribe → Diarize → Detect News → Output

Works locally and in Google Colab!
"""

import os
import json
import sys
from datetime import datetime
from config import OUTPUT_DIR, CHUNK_DURATION, LOOKBACK_DURATION
from youtube_handler import get_latest_stream, download_stream
from transcriber import transcribe_audio, get_segment_text
from diarizer import diarize_audio, match_speaker_to_segment
from news_detector import detect_news

# Optional OCR for speaker names
try:
    from ocr_speaker_detection import process_video_for_ocr, create_speaker_mapping
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# Detect if running in Colab
try:
    import google.colab
    IN_COLAB = True
    print("\n🔵 Running in Google Colab")
except ImportError:
    IN_COLAB = False

def process_stream(audio_path):
    """Volledige pipeline: OCR intro → transcribe HEEL → diarize → detect news"""
    
    print("\n" + "="*60)
    print("🚀 RANST GEMEENTE NEWS DETECTION POC - FULL ANALYSIS")
    print("="*60)
    
    # Use full video/audio
    audio_to_process = audio_path
    
    print(f"📊 Processing FULL video/audio: {os.path.basename(audio_path)}")
    
    # 1. Extract speaker names from intro FIRST (before transcription)
    speaker_mapping = {}
    if OCR_AVAILABLE:
        print("\n[1/4] EXTRACTING SPEAKER NAMES (OCR intro only - ~30 sec)")
        try:
            # Only if it's a video file
            if audio_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
                names = process_video_for_ocr(audio_path)
                if names:
                    print(f"✓ Found {len(names)} speaker names to match")
                else:
                    print("   ℹ️  No names found in intro")
            else:
                print("   ℹ️  Skipping OCR (audio file only)")
        except Exception as e:
            print(f"   ⚠️  OCR failed: {e}")
    
    # 2. Transcribe FULL audio
    print("\n[2/4] TRANSCRIPTION (FULL VIDEO)")
    result = transcribe_audio(audio_to_process)
    segments = result['segments']
    print(f"✓ Transcribed {len(segments)} segments")
    
    # 3. Diarize FULL audio
    print("\n[3/4] SPEAKER DIARIZATION (FULL VIDEO)")
    turns = diarize_audio(audio_to_process)
    
    # Now match names to speakers if OCR succeeded
    if OCR_AVAILABLE and not speaker_mapping:
        try:
            if audio_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
                names = process_video_for_ocr(audio_path)
                if names:
                    speakers = sorted(set(t['speaker'] for t in turns))
                    speaker_mapping = create_speaker_mapping(names, speakers)
                    print(f"✓ Mapped {len(speaker_mapping)} speakers with real names")
        except:
            pass
    
    # 4. Combine + Enrich
    print("\n[4/4] NEWS DETECTION & ANALYSIS")
    enriched_segments = []
    
    for seg in segments:
        speaker = match_speaker_to_segment(seg, turns)
        # Apply OCR mapping if available
        if speaker_mapping and speaker in speaker_mapping:
            speaker = speaker_mapping[speaker]
        
        enriched_segments.append({
            'speaker': speaker,
            'text': seg['text'].strip(),
            'start': round(seg['start'], 2),
            'end': round(seg['end'], 2)
        })
    
    print(f"✓ Enriched {len(enriched_segments)} segments with speaker info")
    
    # 4. Process in 5-minute chunks
    print("\n[4/4] PROCESSING CHUNKS")
    
    audio_duration = max([s['end'] for s in enriched_segments]) if enriched_segments else 0
    chunk_duration_sec = CHUNK_DURATION * 60
    
    news_alerts = []
    
    for chunk_start in range(0, int(audio_duration), int(chunk_duration_sec)):
        chunk_end = min(chunk_start + chunk_duration_sec, audio_duration)
        
        # Get last 30 minutes for analysis
        analysis_start = max(0, chunk_end - (LOOKBACK_DURATION * 60))
        
        # Extract text from this analysis window
        chunk_text = get_segment_text(enriched_segments, analysis_start, chunk_end)
        
        if not chunk_text or len(chunk_text) < 50:
            continue
        
        # Detect news
        news_result = detect_news(chunk_text)
        
        if news_result['is_newsworthy']:
            alert = {
                'timestamp': datetime.now().isoformat(),
                'chunk_time': f"{chunk_start//60}m - {chunk_end//60}m",
                'chunk_seconds': (chunk_start, chunk_end),
                'score': news_result['score'],
                'category': news_result['category'],
                'reason': news_result['reason'],
                'text_preview': chunk_text[:300] + '...',
                'speakers': list(set([s['speaker'] for s in enriched_segments 
                                     if analysis_start <= s['start'] <= chunk_end]))
            }
            news_alerts.append(alert)
            
            print(f"\n🚨 NEWS ALERT at {alert['chunk_time']}")
            print(f"   Score: {alert['score']} | Category: {alert['category']}")
            print(f"   Reason: {alert['reason']}")
    
    # 5. Save results
    output_file = os.path.join(OUTPUT_DIR, f"ranst_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    
    output_data = {
        'metadata': {
            'audio_file': os.path.basename(audio_path),
            'processed_at': datetime.now().isoformat(),
            'total_duration_seconds': audio_duration,
            'segments_count': len(enriched_segments),
            'speakers_count': len(set(s['speaker'] for s in enriched_segments)),
        },
        'news_alerts': news_alerts,
        'full_transcript': enriched_segments
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print("\n" + "="*60)
    print(f"✓ Results saved to: {output_file}")
    print(f"✓ Total news alerts: {len(news_alerts)}")
    print("="*60 + "\n")
    
    return output_data

def main():
    import sys
    
    # Check if audio file is provided as argument
    if len(sys.argv) > 1:
        audio_path = sys.argv[1]
        if not os.path.exists(audio_path):
            print(f"✗ File not found: {audio_path}")
            return
        print(f"📁 Using provided audio file: {audio_path}")
    else:
        print("\n📍 Finding latest Ranst stream...")
        stream = get_latest_stream()
        
        if not stream:
            print("✗ No stream found. Try again later.")
            print("\n💡 Tip: You can test with a local audio file:")
            print("   python main.py /path/to/audio.mp3")
            return
        
        print(f"✓ Found: {stream['title']}")
        print(f"   Duration: {stream['duration']}s" if stream['duration'] else "   (live or unknown duration)")
        
        # Download
        audio_path = download_stream(stream['id'])
        
        if not audio_path or not os.path.exists(audio_path):
            print("✗ Download failed")
            return
    
    # Process
    try:
        process_stream(audio_path)
    except Exception as e:
        print(f"✗ Processing error: {e}")
        raise

if __name__ == '__main__':
    main()
