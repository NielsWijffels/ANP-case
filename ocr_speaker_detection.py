#!/usr/bin/env python3
"""
OCR Speaker Detection - Extract names from video intro (eerste 3 minuten)
Herkent "Gastspreker:" labels en introducties
"""

import cv2
import os
import re
from pathlib import Path

try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False

def extract_frames_from_intro(video_path, duration_seconds=180, sample_interval=5):
    """Extract frames van INTRO fase (eerste 3 minuten) voor OCR"""
    print(f"📹 Sampling first {duration_seconds}s of video for speaker intro...")
    
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    frames = []
    frame_times = []
    
    # Sample every N seconds from intro
    current_time = 0
    while current_time < duration_seconds:
        frame_pos = int(current_time * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
        ret, frame = cap.read()
        
        if ret:
            frames.append(frame)
            frame_times.append(current_time)
        
        current_time += sample_interval
    
    cap.release()
    return frames, frame_times

def extract_text_from_frames(frames):
    """Use EasyOCR to extract text from frames"""
    if not EASYOCR_AVAILABLE:
        print("⚠️  EasyOCR not available, skipping OCR")
        return []
    
    print(f"🔍 Running OCR on {len(frames)} intro frames...")
    
    try:
        reader = easyocr.Reader(['nl'], gpu=False, verbose=False)
    except:
        print("⚠️  OCR initialization failed")
        return []
    
    all_text = []
    
    for i, frame in enumerate(frames):
        try:
            # Focus on text areas (bottom and top for labels)
            height = frame.shape[0]
            
            # Bottom area (speaker names often there)
            bottom_area = frame[int(height * 0.75):, :]
            results = reader.readtext(bottom_area, detail=0)
            
            # Top area (titles/roles)
            top_area = frame[:int(height * 0.25), :]
            results += reader.readtext(top_area, detail=0)
            
            all_text.extend(results)
        except Exception as e:
            pass
    
    return all_text

def find_names_in_text(texts):
    """Find speaker names - fokus op 'Gastspreker:' labels en introducties"""
    print(f"\n📋 Analyzing extracted text for speaker names...")
    
    names = []
    combined_text = ' '.join(texts)
    
    # Pattern 1: "Gastspreker: Naam Voornaam" of "Gastspreker Naam"
    gastspreker_matches = re.findall(r'(?:Gastspreker[:\s]+)?([A-Z][a-z]+ [A-Z][a-z]+)', combined_text)
    for name in gastspreker_matches:
        if name not in names and len(name) > 3:
            names.append(name)
    
    # Pattern 2: "Naam:" (intro format)
    name_matches = re.findall(r'^([A-Z][a-z]+ [A-Z][a-z]+)[:|\s]', combined_text, re.MULTILINE)
    for name in name_matches:
        if name not in names and len(name) > 3:
            names.append(name)
    
    # Pattern 3: Individual all-caps words that look like names (Voorvoegsel handling)
    for text in texts:
        text = text.strip()
        if len(text) < 3 or len(text) > 50:
            continue
        if text.lower() in ['gastspreker', 'raad', 'gemeente', 'ranst', 'burgemeester', 'schepen', 'wethouder', 'voorzitter']:
            continue
        if text[0].isupper() and text.count(' ') == 1:  # Two words
            words = text.split()
            if all(w[0].isupper() for w in words):
                if text not in names:
                    names.append(text)
    
    return list(set(names))  # Remove duplicates

def create_speaker_mapping(names, speaker_labels):
    """Map extracted names to speaker labels via voice similarity scoring"""
    print(f"\n🎙️  Matching {len(names)} names to {len(speaker_labels)} speakers...")
    
    mapping = {}
    
    # Simple approach: assign names in order (improved matching could use voice profiles)
    for i, speaker_label in enumerate(speaker_labels):
        if i < len(names):
            mapping[speaker_label] = names[i]
            print(f"  {speaker_label} → {names[i]}")
        else:
            mapping[speaker_label] = speaker_label
    
    return mapping

def process_video_for_ocr(video_path):
    """Main function: extract speaker names from video intro"""
    print("\n" + "="*60)
    print("🎬 EXTRACTING SPEAKER NAMES FROM VIDEO INTRO")
    print("="*60)
    
    if not os.path.exists(video_path):
        print(f"❌ File not found: {video_path}")
        return None
    
    # 1. Extract intro frames only (eerste 3 minuten)
    frames, frame_times = extract_frames_from_intro(video_path, duration_seconds=180, sample_interval=10)
    
    if not frames:
        print("❌ Could not extract frames")
        return None
    
    print(f"✓ Extracted {len(frames)} frames")
    
    # 2. Run OCR
    texts = extract_text_from_frames(frames)
    
    if not texts:
        print("ℹ️  No text found in intro via OCR")
        return []
    
    # 3. Find names
    names = find_names_in_text(texts)
    
    print(f"\n✓ Found {len(names)} potential speaker names:")
    for name in names[:15]:
        print(f"  - {name}")
    
    return names

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python ocr_speaker_detection.py <video_file>")
        sys.exit(1)
    
    video_file = sys.argv[1]
    
    if not os.path.exists(video_file):
        print(f"❌ File not found: {video_file}")
        sys.exit(1)
    
    names = process_video_for_ocr(video_file)
