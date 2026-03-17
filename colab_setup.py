#!/usr/bin/env python3
"""
Google Colab Setup Script voor RANST POC
Installeert alle dependencies en configureert Ollama
"""

import os
import sys
import subprocess
from pathlib import Path

def run_command(cmd, description=""):
    """Run shell command"""
    if description:
        print(f"\n🔧 {description}...")
    print(f"   Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ⚠️  {result.stderr}")
    return result

def setup_colab():
    """Setup RANST POC for Google Colab"""
    
    print("\n" + "="*60)
    print("🚀 RANST POC - Google Colab Setup")
    print("="*60)
    
    # Check if running in Colab
    try:
        import google.colab
        IN_COLAB = True
        print("✓ Running in Google Colab")
    except ImportError:
        IN_COLAB = False
        print("⚠️  Not running in Google Colab - some features may not work")
    
    # Setup directories
    print("\n📁 Setting up directories...")
    os.makedirs('/content/output', exist_ok=True)
    os.makedirs('/content/temp', exist_ok=True)
    os.makedirs('/content/models', exist_ok=True)
    print("   ✓ Created /content/output, /content/temp, /content/models")
    
    # Install system dependencies
    print("\n📦 Installing system dependencies...")
    run_command("apt-get update -qq", "Update package manager")
    run_command("apt-get install -y -qq ffmpeg", "Install FFmpeg")
    run_command("apt-get install -y -qq curl", "Install curl")
    
    # Install Python packages
    print("\n🐍 Installing Python packages...")
    packages = [
        "openai-whisper",
        "torch",
        "pyannote.audio",
        "yt-dlp",
        "requests",
        "python-dotenv",
    ]
    
    for pkg in packages:
        run_command(f"pip install -q {pkg}", f"Installing {pkg}")
    
    print("   ✓ All packages installed")
    
    # Setup Ollama
    print("\n🦙 Setting up Ollama...")
    
    if IN_COLAB:
        # Install Ollama in Colab
        run_command(
            "curl -fsSL https://ollama.ai/install.sh | sh -s -- --insecure",
            "Download Ollama"
        )
        
        # Create startup script
        startup_script = """#!/bin/bash
echo "Starting Ollama service..."
ollama serve &
OLLAMA_PID=$!

# Wait for Ollama to start
sleep 5

# Pull model
echo "Pulling mistral model..."
ollama pull mistral

# Keep running
wait $OLLAMA_PID
"""
        
        with open('/content/start_ollama.sh', 'w') as f:
            f.write(startup_script)
        
        os.chmod('/content/start_ollama.sh', 0o755)
        print("   ✓ Created startup script at /content/start_ollama.sh")
        
        print("\n⚡ To use Ollama in your Colab notebook:")
        print("   1. Run this in a cell:")
        print('      !bash /content/start_ollama.sh &')
        print("   2. Wait 30 seconds for Ollama to start")
        print("   3. Run your analysis")
    else:
        print("   ⚠️  Ollama setup skipped (not in Colab)")
        print("   Make sure Ollama is running locally: ollama serve")
    
    # Create .env for Colab
    print("\n📝 Creating .env file...")
    env_content = """HF_TOKEN=your_huggingface_token_here
OLLAMA_MODEL=mistral
OLLAMA_BASE_URL=http://localhost:11434
NEWS_SCORE_THRESHOLD=0.65
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    print("   ✓ Created .env file")
    print("   ⚠️  Please add your HuggingFace token to .env")
    
    # Summary
    print("\n" + "="*60)
    print("✅ Setup Complete!")
    print("="*60)
    print("\n📋 Next steps:")
    print("1. Set HF_TOKEN in .env from https://huggingface.co/settings/tokens")
    print("2. Start Ollama (if in Colab): !bash /content/start_ollama.sh &")
    print("3. Wait 30 seconds")
    print("4. Run: !python main.py")
    print("\n🎯 Output will be saved to: /content/output/")
    print("="*60 + "\n")

if __name__ == '__main__':
    setup_colab()
