#!/usr/bin/env python3
"""
Speaker Name Mapper - Voeg echte namen toe aan Speaker 1, Speaker 2, etc.
"""

import json
import os
import sys
from pathlib import Path

def load_result(json_file):
    """Load JSON result file"""
    with open(json_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def show_speaker_preview(data, speaker, num_samples=3):
    """Show sample quotes from a speaker"""
    transcript = data['full_transcript']
    speaker_quotes = [s['text'] for s in transcript if s['speaker'] == speaker]
    
    print(f"\n🎤 Samples from {speaker}:")
    for i, quote in enumerate(speaker_quotes[:num_samples], 1):
        preview = quote[:80] + "..." if len(quote) > 80 else quote
        print(f"   {i}. {preview}")

def interactive_mapping(json_file):
    """Interactive mode: Ask user to map speakers to names"""
    data = load_result(json_file)
    transcript = data['full_transcript']
    
    # Get unique speakers
    speakers = sorted(set(s['speaker'] for s in transcript))
    
    print("\n" + "="*60)
    print("🎙️  SPEAKER IDENTIFICATION")
    print("="*60)
    print(f"\nFound {len(speakers)} speakers in the video:\n")
    
    mapping = {}
    
    for speaker in speakers:
        print(f"\n{speaker}:")
        show_speaker_preview(data, speaker, num_samples=3)
        
        name = input(f"\n✏️  Name for {speaker} (or press Enter to keep): ").strip()
        
        if name:
            mapping[speaker] = name
        else:
            mapping[speaker] = speaker  # Keep original
    
    return mapping

def apply_mapping(json_file, mapping):
    """Apply speaker name mapping to entire JSON"""
    data = load_result(json_file)
    
    # Update full_transcript
    for segment in data['full_transcript']:
        old_speaker = segment['speaker']
        segment['speaker'] = mapping.get(old_speaker, old_speaker)
    
    # Update news_alerts
    for alert in data['news_alerts']:
        alert['speakers'] = [mapping.get(s, s) for s in alert.get('speakers', [])]
    
    return data

def save_mapped_result(data, output_file):
    """Save updated JSON with speaker names"""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✓ Saved to: {output_file}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python speaker_mapper.py <json_file> [output_file]")
        print("\nExample:")
        print("  python speaker_mapper.py output/ranst_news_20260317_103000.json")
        sys.exit(1)
    
    json_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else json_file.replace('.json', '_mapped.json')
    
    if not os.path.exists(json_file):
        print(f"❌ File not found: {json_file}")
        sys.exit(1)
    
    # Interactive mapping
    mapping = interactive_mapping(json_file)
    
    print("\n" + "="*60)
    print("Applied mapping:")
    for old, new in mapping.items():
        if old != new:
            print(f"  {old} → {new}")
    
    # Apply and save
    data = apply_mapping(json_file, mapping)
    save_mapped_result(data, output_file)
    
    print("\n🎉 Done! Now you can view the results with proper speaker names.")

if __name__ == '__main__':
    main()
