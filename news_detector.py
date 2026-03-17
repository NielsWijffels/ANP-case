import requests
import json
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, NEWS_SCORE_THRESHOLD
from datetime import datetime

NEWS_DETECTION_PROMPT = """Je bent een nieuwsjournalist die werkt voor het Nederlands persbureau ANP.
Je taak: bepaal of een transcript van gemeenteraadsvergadering NIEUWSWAARDIG is.

NIEUWSWAARDIG = interessant voor het brede publiek, beleid dat impact heeft, besluiten, controverses, belangrijke mededelingen

NIET NIEUWSWAARDIG = procedurele opmerkingen, technische details zonder impact, agenda punten

Analyse het volgende transcript en antwoord EXACT in dit format:
[SCORE: X.XX]
[REASON: korte reden waarom wel of niet nieuwswaardig]
[CATEGORY: politiek|economie|milieu|onderwijs|gezondheid|veiligheid|overig]

Score is 0-1, waarbij 1 = zeer nieuwswaardig, 0 = niet nieuwswaardig.

TRANSCRIPT:
{transcript}

ANTWOORD:"""

def detect_news(transcript):
    """Gebruik local LLM om nieuwswaardigheid te bepalen"""
    
    if not transcript or len(transcript.strip()) < 50:
        return {
            'score': 0.0,
            'reason': 'Tekst te kort',
            'category': 'overig',
            'is_newsworthy': False
        }
    
    print(f"🔍 Analyzing {len(transcript)} chars with {OLLAMA_MODEL}...")
    
    prompt = NEWS_DETECTION_PROMPT.format(transcript=transcript[:2000])
    
    try:
        response = requests.post(
            f'{OLLAMA_BASE_URL}/api/generate',
            json={
                'model': OLLAMA_MODEL,
                'prompt': prompt,
                'stream': False,
                'temperature': 0.3,
            },
            timeout=120
        )
        
        if response.status_code != 200:
            print(f"✗ Ollama error: {response.status_code}")
            return {'score': 0.0, 'reason': 'LLM error', 'is_newsworthy': False}
        
        output = response.json()['response']
        
        # Parse output
        lines = output.strip().split('\n')
        score = 0.0
        reason = ''
        category = 'overig'
        
        for line in lines:
            if '[SCORE:' in line:
                try:
                    score = float(line.split('[SCORE:')[1].split(']')[0].strip())
                    score = min(1.0, max(0.0, score))
                except:
                    score = 0.5
            elif '[REASON:' in line:
                reason = line.split('[REASON:')[1].split(']')[0].strip()
            elif '[CATEGORY:' in line:
                category = line.split('[CATEGORY:')[1].split(']')[0].strip()
        
        is_newsworthy = score >= NEWS_SCORE_THRESHOLD
        
        result = {
            'score': round(score, 2),
            'reason': reason,
            'category': category,
            'is_newsworthy': is_newsworthy,
            'timestamp': datetime.now().isoformat()
        }
        
        print(f"  Score: {score:.2f} | Category: {category} | Newsworthy: {is_newsworthy}")
        
        return result
        
    except requests.exceptions.ConnectionError:
        print("✗ Cannot connect to Ollama. Is it running? (ollama serve)")
        return {'score': 0.0, 'reason': 'Ollama not available', 'is_newsworthy': False}
    except Exception as e:
        print(f"✗ Error: {e}")
        return {'score': 0.0, 'reason': f'Error: {str(e)}', 'is_newsworthy': False}

if __name__ == '__main__':
    test_text = """De gemeenteraad stemde met grote meerderheid in met het nieuwe bouwplan voor het centrum. 
    Dit zal duizenden nieuwe woningen opleveren. GroenLinks opperde twijfels over de duurzaamheid."""
    
    result = detect_news(test_text)
    print(json.dumps(result, indent=2))
