import yt_dlp
import os
from config import TEMP_DIR
from datetime import datetime

def get_latest_stream():
    """Haalt meest recente video/stream van Ranst YouTube channel"""
    ydl_opts = {
        'quiet': False,
        'no_warnings': False,
        'socket_timeout': 30,
        'skip_unavailable_fragments': True,
        'fragment_retries': 0,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Haal alle uploads van kanaal (niet alleen streams)
            info = ydl.extract_info('https://www.youtube.com/@gemeenteranst1107/videos', download=False)
            
            if info and 'entries' in info:
                for entry in info['entries']:
                    if not entry or 'id' not in entry:
                        continue
                    
                    video_id = entry['id']
                    print(f"📺 Trying: {entry.get('title', video_id)[:60]}")
                    
                    # Probeer het video-object op te halen
                    try:
                        video_info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                        
                        if video_info:
                            return {
                                'id': video_id,
                                'title': entry.get('title', 'Unknown'),
                                'url': f"https://www.youtube.com/watch?v={video_id}",
                                'duration': entry.get('duration', None),
                            }
                    except Exception as e:
                        # Skip deze video
                        if "not available" in str(e).lower() or "begin in" in str(e).lower():
                            print(f"   ⏳ Not available yet, skipping...")
                        else:
                            print(f"   ⚠️  Error, trying next...")
                        continue
        except Exception as e:
            print(f"Error getting video list: {e}")
            pass
    
    return None

def download_stream(video_id, output_path=None):
    """Download YouTube stream naar audio (video extract)"""
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(TEMP_DIR, f'ranst_{timestamp}')
    
    print(f"🎥 Downloading: https://www.youtube.com/watch?v={video_id}")
    print("   📻 Extracting audio only (sneller)...")
    
    ydl_opts = {
        'format': 'bestaudio/best',  # Alleen audio, geen video
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': output_path,
        'quiet': False,
        'no_warnings': False,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        
        # Find the downloaded file
        mp3_file = f"{output_path}.mp3"
        if os.path.exists(mp3_file):
            print(f"✓ Downloaded to: {mp3_file}")
            return mp3_file
        
        # Check if it's using a different extension
        for ext in ['.m4a', '.webm', '.mp4', '.wav']:
            alt_file = f"{output_path}{ext}"
            if os.path.exists(alt_file):
                print(f"✓ Downloaded to: {alt_file}")
                return alt_file
        
        print(f"✗ Download seems to have failed - no output file found")
        return None
        
    except Exception as e:
        print(f"✗ Download failed: {e}")
        return None

if __name__ == '__main__':
    stream = get_latest_stream()
    if stream:
        print(f"Found stream: {stream['title']}")
        download_stream(stream['id'])
