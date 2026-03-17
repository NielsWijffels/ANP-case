import torch
from pyannote.audio import Pipeline
from config import HF_TOKEN

def diarize_audio(audio_path):
    """Voer speaker diarization uit (GPU optimized)"""
    print("🎙️ Loading diarization pipeline...")
    
    pipeline = Pipeline.from_pretrained(
        'pyannote/speaker-diarization-3.1',
        use_auth_token=HF_TOKEN
    )
    
    # Auto-detect best device - Force CPU for better compatibility
    # Some operations in pyannote don't work well on MPS
    if torch.cuda.is_available():
        device = torch.device('cuda')
        print("✓ Using CUDA GPU")
    else:
        device = torch.device('cpu')
        print("✓ Using CPU")
    
    pipeline = pipeline.to(device)
    
    print(f"🎙️ Diarizing: {audio_path}")
    diarization = pipeline(audio_path)
    
    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append({
            'speaker': speaker,
            'start': round(turn.start, 2),
            'end': round(turn.end, 2)
        })
    
    speakers_count = len(set(t['speaker'] for t in turns))
    print(f"✓ Found {speakers_count} speakers")
    return turns
    
    print(f"🎙️ Diarizing: {audio_path}")
    diarization = pipeline(audio_path)
    
    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append({
            'speaker': speaker,
            'start': round(turn.start, 2),
            'end': round(turn.end, 2)
        })
    
    speakers_count = len(set(t['speaker'] for t in turns))
    print(f"✓ Found {speakers_count} speakers")
    return turns

def match_speaker_to_segment(segment, turns):
    """Match transcript segment to speaker"""
    seg_start = segment.get('start', 0)
    seg_end = segment.get('end', 0)
    
    best_speaker = 'Onbekend'
    best_overlap = 0.0
    
    for turn in turns:
        overlap = max(0.0, min(seg_end, turn['end']) - max(seg_start, turn['start']))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn['speaker']
    
    return best_speaker

if __name__ == '__main__':
    test_file = './temp/test.mp3'
    import os
    if os.path.exists(test_file):
        turns = diarize_audio(test_file)
        for t in turns[:5]:
            print(t)
