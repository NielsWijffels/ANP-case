#!/usr/bin/env python3
"""
Streaming architecture for ANP live alerts
Processes video in 5-min chunks with parallel processing
Optimized for Google Colab GPU
"""

import os
import json
import subprocess
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from config import OUTPUT_DIR, TEMP_DIR, CHUNK_DURATION
from youtube_handler import get_latest_stream, download_stream
from transcriber import transcribe_audio, get_segment_text
from diarizer import diarize_audio, match_speaker_to_segment
from news_detector import detect_news

try:
    from ocr_speaker_detection import process_video_for_ocr, create_speaker_mapping
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# Lock for thread-safe file operations
output_lock = threading.Lock()

class StreamingProcessor:
    """Live streaming processor for council meetings"""
    
    def __init__(self, video_path, output_dir=OUTPUT_DIR):
        self.video_path = os.path.abspath(video_path)  # Use absolute path
        self.output_dir = os.path.abspath(output_dir)  # Use absolute path
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.session_id = f"ranst_news_{self.timestamp}"
        self.speaker_map = {}
        self.all_segments = []
        self.news_alerts = []
        self.chunk_num = 0
        
        os.makedirs(self.output_dir, exist_ok=True)
    
    def extract_speaker_names_intro(self):
        """PHASE 1: Extract names from first 5 minutes"""
        print("\n" + "="*60)
        print("🎬 PHASE 1: EXTRACTING SPEAKER NAMES (first 5 min)")
        print("="*60)
        
        if not OCR_AVAILABLE:
            print("⚠️  OCR not available, using generic labels")
            return {}
        
        try:
            if self.video_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
                names = process_video_for_ocr(self.video_path)
                if names:
                    print(f"✓ Found {len(names)} speaker names")
                    return names
        except Exception as e:
            print(f"⚠️  OCR failed: {e}")
        
        return {}
    
    def split_into_chunks(self, chunk_size_minutes=5):
        """Split video into chunks for processing"""
        print("\n[Prep] Splitting video into 5-min chunks...")
        
        # Get video duration
        result = subprocess.run([
            'ffprobe', '-v', 'error', 
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            self.video_path
        ], capture_output=True, text=True)
        
        try:
            duration_secs = float(result.stdout.strip())
        except:
            print("❌ Could not determine video duration")
            return []
        
        chunk_size_secs = chunk_size_minutes * 60
        chunks = []
        
        for start_sec in range(0, int(duration_secs), int(chunk_size_secs)):
            end_sec = min(start_sec + chunk_size_secs, duration_secs)
            chunks.append({
                'num': len(chunks) + 1,
                'start': start_sec,
                'end': end_sec,
                'duration': end_sec - start_sec
            })
        
        print(f"✓ Split into {len(chunks)} chunks")
        return chunks
    
    def extract_chunk(self, chunk_info):
        """Extract audio chunk from video"""
        chunk_num = chunk_info['num']
        start_sec = chunk_info['start']
        duration_sec = chunk_info['duration']
        
        output_file = os.path.join(os.path.abspath(TEMP_DIR), f'chunk_{chunk_num:03d}.wav')
        
        try:
            subprocess.run([
                'ffmpeg', '-i', self.video_path,
                '-ss', str(start_sec),
                '-t', str(duration_sec),
                '-q:a', '9', '-n',
                output_file
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300, check=True)
            
            if os.path.exists(output_file):
                return output_file
            else:
                print(f"  ❌ Chunk file not created: {output_file}")
                return None
        except subprocess.TimeoutExpired:
            print(f"  ❌ FFmpeg timeout for chunk {chunk_num}")
            return None
        except Exception as e:
            print(f"  ❌ FFmpeg error for chunk {chunk_num}: {e}")
            return None
    
    def process_chunk(self, chunk_info, chunk_audio_path, known_names=None):
        """Process single chunk: transcribe + diarize + analyze"""
        chunk_num = chunk_info['num']
        start_time = chunk_info['start']
        
        print(f"\n[Chunk {chunk_num}] Processing {chunk_info['start']:.0f}s - {chunk_info['end']:.0f}s...")
        
        # Ensure absolute path
        chunk_audio_path = os.path.abspath(chunk_audio_path) if chunk_audio_path else None
        if not chunk_audio_path or not os.path.exists(chunk_audio_path):
            print(f"  ❌ Audio file not found: {chunk_audio_path}")
            return {'alert': None, 'segments': []}
        
        try:
            # Transcribe
            print(f"  [Step 1/3] Transcribing...")
            result = transcribe_audio(chunk_audio_path)
            segments = result['segments']
            print(f"  [Step 1/3] ✓ Transcribed {len(segments)} segments")
            
            if not segments:
                print(f"  ⚠️  No segments from transcription")
                return {'alert': None, 'segments': []}
            
            # Diarize (skip if not available - use sequential speaker detection)
            print(f"  [Step 2/3] Speaker detection...")
            try:
                turns = diarize_audio(chunk_audio_path)
                print(f"  [Step 2/3] ✓ Diarized {len(set(t['speaker'] for t in turns))} speakers")
            except Exception as e:
                print(f"  [Step 2/3] ⚠️  Diarizer unavailable ({type(e).__name__}), using fallback")
                turns = []
            
            # Enrich with speaker names
            enriched_segments = []
            speaker_counter = {'current': 0}
            current_speaker = 'Speaker 1'
            
            for seg in segments:
                if turns:
                    speaker = match_speaker_to_segment(seg, turns)
                    if known_names and speaker in known_names:
                        speaker = known_names[speaker]
                else:
                    # Fallback: assign speakers based on time gaps
                    if seg['start'] > 5.0 and speaker_counter['current'] < 5:
                        speaker_counter['current'] += 1
                    current_speaker = f"Speaker {speaker_counter['current'] + 1}"
                    speaker = current_speaker
                
                enriched_segments.append({
                    'chunk': chunk_num,
                    'speaker': speaker,
                    'text': seg['text'].strip(),
                    'start': start_time + seg['start'],
                    'end': start_time + seg['end']
                })
            
            # Detect news
            print(f"  [Step 3/3] Detecting news...")
            chunk_text = ' '.join([s['text'] for s in enriched_segments])
            
            if chunk_text.strip():
                news_result = detect_news(chunk_text)
                print(f"  [Step 3/3] ✓ Score: {news_result['score']:.2f}")
                
                if news_result['is_newsworthy']:
                    alert = {
                        'timestamp': datetime.now().isoformat(),
                        'chunk': chunk_num,
                        'chunk_time': f"{chunk_info['start']/60:.0f}m - {chunk_info['end']/60:.0f}m",
                        'score': news_result['score'],
                        'category': news_result['category'],
                        'reason': news_result['reason'],
                        'speakers': list(set([s['speaker'] for s in enriched_segments])),
                        'text_preview': chunk_text[:200] + '...'
                    }
                    
                    print(f"  🚨 NEWS ALERT: {alert['reason']} (score: {alert['score']})")
                    return {
                        'alert': alert,
                        'segments': enriched_segments
                    }
            else:
                print(f"  ⚠️  No text to analyze")
            
            return {
                'alert': None,
                'segments': enriched_segments
            }
            
        except Exception as e:
            print(f"  ❌ Error processing chunk: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return {'alert': None, 'segments': []}
    
    def generate_email_mock(self, alert, chunk_num):
        """Generate email alert mock"""
        email_content = f"""
================================================================================
📧 EMAIL ALERT #{chunk_num}
================================================================================

TO: redactie@anp.nl
SUBJECT: 🚨 RANST Raadsvergadering - Nieuwsalert #{chunk_num}
SENT: {alert['timestamp']}

================================================================================
ALERT DETAILS
================================================================================

Time in meeting: {alert['chunk_time']}
News Score: {alert['score']}/1.0
Category: {alert['category']}

Reason: {alert['reason']}

Speakers: {', '.join(alert['speakers'])}

Content Preview:
{alert['text_preview']}

================================================================================
NEXT UPDATE IN ~5 MINUTES
================================================================================
"""
        return email_content
    
    def save_alert(self, alert, chunk_num):
        """Save alert to mock email file"""
        if not alert:
            return
        
        email_file = os.path.join(self.output_dir, f"{self.session_id}_email_alert_{chunk_num:03d}.txt")
        
        with output_lock:
            with open(email_file, 'w', encoding='utf-8') as f:
                f.write(self.generate_email_mock(alert, chunk_num))
        
        print(f"  💾 Saved alert to: {email_file}")
    
    def save_streaming_results(self, segment_data, alert):
        """Save live streaming results"""
        if segment_data:
            self.all_segments.extend(segment_data)
        
        if alert:
            self.news_alerts.append(alert)
        
        # Update live JSON
        live_data = {
            'status': 'processing',
            'timestamp': datetime.now().isoformat(),
            'chunks_processed': len(self.news_alerts),
            'news_alerts': self.news_alerts,
            'segments_processed': len(self.all_segments)
        }
        
        live_file = os.path.join(self.output_dir, f"{self.session_id}_live.json")
        
        with output_lock:
            with open(live_file, 'w', encoding='utf-8') as f:
                json.dump(live_data, f, ensure_ascii=False, indent=2)
    
    def process_streaming(self):
        """Main streaming processing loop"""
        print("\n" + "="*60)
        print("🎬 PHASE 2: STREAMING CHUNK PROCESSING")
        print("="*60)
        
        # Phase 1: Extract names
        names = self.extract_speaker_names_intro()
        
        # Split into chunks
        chunks = self.split_into_chunks(chunk_size_minutes=5)
        
        if not chunks:
            print("❌ No chunks to process")
            return
        
        print(f"\n📊 Processing {len(chunks)} chunks with parallel extraction...\n")
        
        # Extract all chunks in parallel
        chunk_files = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}
            for chunk in chunks:
                future = executor.submit(self.extract_chunk, chunk)
                futures[future] = chunk
            
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    chunk_file = future.result()
                    if chunk_file:
                        chunk_files[chunk['num']] = chunk_file
                        print(f"  ✓ Extracted chunk {chunk['num']}")
                except Exception as e:
                    print(f"  ❌ Failed to extract chunk {chunk['num']}: {e}")
        
        # Process chunks sequentially (maintain order for speaker tracking)
        for chunk in chunks:
            if chunk['num'] not in chunk_files:
                print(f"\n[Chunk {chunk['num']}] ⚠️  Audio file not found, skipping")
                continue
            
            chunk_audio = chunk_files[chunk['num']]
            
            # Process chunk
            result = self.process_chunk(chunk, chunk_audio, names)
            
            # Save results
            self.save_streaming_results(result['segments'], result['alert'])
            
            # Mock email if newsworthy
            if result['alert']:
                self.save_alert(result['alert'], chunk['num'])
            
            # Cleanup chunk file
            try:
                os.remove(chunk_audio)
            except:
                pass
        
        # Final results
        self.save_final_results()
    
    def save_final_results(self):
        """Save final comprehensive results"""
        print("\n" + "="*60)
        print("✅ PHASE 3: FINALIZING RESULTS")
        print("="*60)
        
        output_file = os.path.join(self.output_dir, f"{self.session_id}.json")
        
        final_data = {
            'metadata': {
                'source': os.path.basename(self.video_path),
                'processed_at': datetime.now().isoformat(),
                'total_segments': len(self.all_segments),
                'total_alerts': len(self.news_alerts),
                'session_id': self.session_id
            },
            'news_alerts': self.news_alerts,
            'segments': self.all_segments
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        
        print(f"✓ Final results: {output_file}")
        print(f"\n📊 SUMMARY:")
        print(f"  ✓ Total segments: {len(self.all_segments)}")
        print(f"  🚨 News alerts: {len(self.news_alerts)}")
        
        if self.news_alerts:
            print(f"\n🎯 Top alerts:")
            for i, alert in enumerate(self.news_alerts[:5], 1):
                print(f"  {i}. {alert['reason']} (score: {alert['score']})")


def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python streaming_main.py <video_file>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    
    if not os.path.exists(video_path):
        print(f"❌ File not found: {video_path}")
        sys.exit(1)
    
    processor = StreamingProcessor(video_path)
    processor.process_streaming()


if __name__ == '__main__':
    main()
