#!/usr/bin/env python3
"""Debug single chunk processing"""
import sys
sys.path.insert(0, '/Users/Shared/ranst-poc')

print("="*60)
print("DEBUG RUN - First chunk only")
print("="*60)

try:
    from streaming_main import StreamingProcessor
    
    video_path = '/Users/Shared/ranst-poc/temp/04.03.26 Amsterdam WV HZ.mp4'
    processor = StreamingProcessor(video_path)
    
    # Process ALL chunks
    print("\n[1] Splitting...")
    chunks = processor.split_into_chunks()
    print(f"✓ Got {len(chunks)} chunks")
    
    # Import tqdm
    from tqdm import tqdm
    import os
    
    print("\n[2] Processing all chunks...")
    pbar = tqdm(chunks, desc="Processing", unit="chunk")
    for chunk in pbar:
        chunk_file = processor.extract_chunk(chunk)
        if not chunk_file:
            pbar.write(f"  ⚠️  Chunk {chunk['num']} extraction failed")
            continue
        
        result = processor.process_chunk(chunk, chunk_file)
        processor.all_segments.extend(result['segments'])
        if result['alert']:
            processor.news_alerts.append(result['alert'])
            pbar.write(f"  🚨 Alert in chunk {chunk['num']}")
        
        try:
            os.remove(chunk_file)
        except:
            pass
    
    print(f"\n✓ Processed {len(processor.all_segments)} total segments")
    
    print("\n[4] Saving...")
    processor.save_streaming_results(result['segments'], result['alert'])
    processor.save_final_results()
    
    print("\n✅ SUCCESS!")
    
    import os
    print(f"\n[Output files]:")
    for f in os.listdir('/Users/Shared/ranst-poc/output/'):
        size = os.path.getsize(f'/Users/Shared/ranst-poc/output/{f}')
        print(f"  - {f} ({size/1024:.1f}KB)")

except Exception as e:
    print(f"\n❌ ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
