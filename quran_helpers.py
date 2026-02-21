import requests
import io
import os
from typing import List, Dict

# Optional imports (only used when keys/packages are available)
try:
    import openai
except Exception:
    openai = None

try:
    from gtts import gTTS
except Exception:
    gTTS = None


def fetch_surah(surah_number: int, edition: str = "en.sahih") -> dict:
    """Fetch full surah (JSON) from alquran.cloud. Returns dict or raises."""
    url = f"https://api.alquran.cloud/v1/surah/{surah_number}/{edition}"
    r = requests.get(url, timeout=12)
    r.raise_for_status()
    return r.json()


def fetch_surah_list() -> List[Dict]:
    """Fetch metadata for all 114 surahs (number, name, englishName, numberOfAyahs).
    Falls back to a small built-in list if the API is unreachable.
    Returns a list of dicts with keys: number, name, englishName, numberOfAyahs, revelationType
    """
    try:
        url = "https://api.alquran.cloud/v1/surahs"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data and data.get('data'):
            out = []
            for s in data['data']:
                out.append({
                    'number': s.get('number'),
                    'name': s.get('name'),
                    'englishName': s.get('englishName'),
                    'englishNameTranslation': s.get('englishNameTranslation'),
                    'numberOfAyahs': s.get('numberOfAyahs'),
                    'revelationType': s.get('revelationType')
                })
            return out
    except Exception:
        pass
    # fallback minimal list (some entries; app will still fetch individual surahs on demand)
    fallback = []
    names = [
        (1, 'الفاتحة', 'Al-Faatiha', 'The Opening', 7, 'Meccan'),
        (2, 'البقرة', 'Al-Baqara', 'The Cow', 286, 'Medinan'),
        (3, 'آل عمران', 'Aal-i-Imraan', 'The Family of Imran', 200, 'Medinan'),
        (4, 'النساء', 'An-Nisaa', 'The Women', 176, 'Medinan'),
        (5, 'المائدة', 'Al-Maaida', 'The Table', 120, 'Medinan'),
        (6, 'الأنعام', 'Al-An\'am', 'The Cattle', 165, 'Meccan'),
    ]
    for n, name, eng, engtr, count, rev in names:
        fallback.append({'number': n, 'name': name, 'englishName': eng, 'englishNameTranslation': engtr, 'numberOfAyahs': count, 'revelationType': rev})
    return fallback


def fetch_editions() -> List[str]:
    """Try to fetch available editions/translations from the Quran API. Returns a list of edition identifiers.
    Falls back to a small built-in list on error."""
    try:
        url = "https://api.alquran.cloud/v1/editions"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data and data.get('data'):
            # return edition identifier like 'en.sahih'
            editions = [e.get('identifier') for e in data['data'] if e.get('identifier')]
            return sorted(list(set(editions)))
    except Exception:
        pass
    # fallback
    return ["en.sahih", "en.asad", "en.pickthall", "en.yusufali", "ar.alafasy"]


def tts_generate_mp3_bytes(text: str, lang: str = 'ar') -> bytes:
    """Generate an MP3 from text using gTTS and return raw bytes. Raises if gTTS not available."""
    if not gTTS:
        raise RuntimeError("gTTS is not installed. Add gTTS to requirements to enable TTS.")
    tts = gTTS(text=text, lang=lang)
    bio = io.BytesIO()
    tts.write_to_fp(bio)
    bio.seek(0)
    return bio.read()


def tts_cached_mp3_bytes(username: str, surah: int, ayah: int, text: str, lang: str = 'ar', cache_dir: str = None) -> bytes:
    """Generate or retrieve cached TTS MP3 bytes for a user+surah+ayah. Returns bytes.
    Cache path defaults to ./tts_cache/{username}_{surah}_{ayah}.mp3
    Requires gTTS to be installed."""
    if cache_dir is None:
        cache_dir = os.path.join(os.getcwd(), 'tts_cache')
    os.makedirs(cache_dir, exist_ok=True)
    safe_user = (username or 'anon').replace('..', '').replace('/', '_')
    fname = f"{safe_user}_{surah}_{ayah}.mp3"
    path = os.path.join(cache_dir, fname)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read()
    # generate and save
    mp3 = tts_generate_mp3_bytes(text, lang=lang)
    try:
        with open(path, 'wb') as f:
            f.write(mp3)
    except Exception:
        # ignore write errors but still return bytes
        pass
    return mp3


def fetch_reciter_audio_by_url(base_url: str, surah: int, ayah: int, timeout: float = 10.0) -> bytes:
    """Attempt to download reciter audio from a user-provided base_url. The base_url may contain
    placeholders {surah} and {ayah}. If not, the helper will try a couple of common patterns.
    Returns bytes on success or raises an exception."""
    # format with placeholders if present
    candidates = []
    try:
        if '{surah}' in base_url or '{ayah}' in base_url:
            candidates.append(base_url.format(surah=surah, ayah=ayah))
        else:
            # try common patterns
            candidates.append(base_url.rstrip('/') + f"/{surah}_{ayah}.mp3")
            candidates.append(base_url.rstrip('/') + f"/{surah}/{ayah}.mp3")
            candidates.append(base_url.rstrip('/') + f"/{surah}0{ayah}.mp3")
    except Exception:
        candidates.append(base_url)

    last_err = None
    for url in candidates:
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200 and r.headers.get('content-type', '').startswith('audio'):
                return r.content
        except Exception as e:
            last_err = e
            continue
    # if none succeeded, raise last error or a generic one
    if last_err:
        raise last_err
    raise RuntimeError('Reciter audio not found at provided URL patterns')


def fetch_ayah(surah_number: int, ayah_number: int, edition: str = "en.sahih") -> dict:
    """Fetch a single ayah using the surah endpoint and select the ayah."""
    data = fetch_surah(surah_number, edition)
    if data and data.get("data"):
        ayahs = data["data"].get("ayahs", [])
        for ay in ayahs:
            if ay.get("numberInSurah") == ayah_number:
                return ay
    return None


def summarize_text(text: str, openai_key: str = None, max_tokens: int = 200) -> str:
    """Return a short summary. If OpenAI key provided and openai package available, use it; otherwise fallback."""
    if openai_key and openai:
        try:
            openai.api_key = openai_key
            # Use ChatCompletion for better results
            resp = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": "You are a concise assistant that summarizes Quranic passages respectfully."},
                          {"role": "user", "content": f"Summarize the following passage in 3-4 short sentences: {text}"}],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            return resp["choices"][0]["message"]["content"].strip()
        except Exception:
            pass
    # Fallback: simple extractive summary (first 2-3 sentences)
    s = text.replace('\n', ' ').strip()
    # naive sentence split
    parts = s.split('. ')
    return ('. '.join(parts[:3]) + ('.' if len(parts) > 0 else '')).strip()
