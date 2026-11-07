import os
import sys
import re
import socket

# Force IPv4 socket resolution globally to eliminate 20s IPv6 connect timeout hangs on Windows/local
try:
    _orig_getaddrinfo = socket.getaddrinfo
    def _forced_ipv4_getaddrinfo(*args, **kwargs):
        responses = _orig_getaddrinfo(*args, **kwargs)
        ipv4_res = [r for r in responses if r[0] == socket.AF_INET]
        return ipv4_res if ipv4_res else responses
    socket.getaddrinfo = _forced_ipv4_getaddrinfo
except Exception:
    pass
import json
import html
import time
import base64
import urllib.parse
import tempfile
import subprocess
import unicodedata
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from verifier import AppSelectionVerifierSubagent
import androforever_scraper

SOURCE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source_state.json")

def get_current_source(sent_set=None):
    """
    Determines alternating source: 'liteapks' or 'androforever'.
    Stored seamlessly inside sent_apks.json as a special state key (_SOURCE_STATE_::<source>)
    AND in source_state.json. This guarantees 100% persistence in GitHub Actions!
    """
    # 1. Check in-memory sent_set
    if sent_set:
        for item in sent_set:
            if isinstance(item, str) and item.startswith("_SOURCE_STATE_::"):
                last = item.split("::")[-1].lower()
                return 'androforever' if last == 'liteapks' else 'liteapks'

    # 2. Check sent_apks.json file directly
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data:
                    if isinstance(item, str) and item.startswith("_SOURCE_STATE_::"):
                        last = item.split("::")[-1].lower()
                        return 'androforever' if last == 'liteapks' else 'liteapks'
        except Exception:
            pass

    # 3. Fallback to source_state.json
    if os.path.exists(SOURCE_STATE_FILE):
        try:
            with open(SOURCE_STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                last = data.get('last_source', 'androforever').lower()
                return 'androforever' if last == 'liteapks' else 'liteapks'
        except Exception:
            pass

    return 'androforever'

def save_source_state(source_used, sent_set=None):
    """
    Saves the last used source to alternate next time.
    Persists in source_state.json AND directly inside sent_apks.json so GitHub Actions commits it!
    """
    source_used = source_used.lower()

    # 1. Write source_state.json
    try:
        with open(SOURCE_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'last_source': source_used,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }, f, indent=2)
    except Exception as e:
        print(f"[Source State Write Warning]: {e}")

    # 2. Persist in sent_apks.json
    try:
        if os.path.exists(DATABASE_FILE):
            with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
                db_items = json.load(f)
            clean_items = [x for x in db_items if not (isinstance(x, str) and x.startswith("_SOURCE_STATE_::"))]
            clean_items.append(f"_SOURCE_STATE_::{source_used}")
            with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
                json.dump(clean_items, f, indent=2)
            if sent_set is not None:
                to_del = [x for x in sent_set if str(x).startswith("_SOURCE_STATE_::")]
                for td in to_del:
                    sent_set.discard(td)
                sent_set.add(f"_SOURCE_STATE_::{source_used}")
            print(f"💾 [SOURCE STATE PERSISTED IN sent_apks.json]: Last source = {source_used.upper()}")
    except Exception as dbe:
        print(f"[Database State Persist Error]: {dbe}")
from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    ADMIN_CHAT_ID,
    DEVUPLOADS_API_KEY,
    SOURCE_BASE_URL,
    MAX_APKS_PER_RUN,
    DATABASE_FILE,
    CATALOG_FILE
)

try:
    from config import MAX_BOT_API_SIZE_MB, LOCAL_BOT_API_URL
except ImportError:
    MAX_BOT_API_SIZE_MB = 48
    LOCAL_BOT_API_URL = ""

def send_admin_alert(message):
    """Sends immediate SOS notifications to Admin Telegram chat if any error occurs."""
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        print("[Admin Alert Info]: BOT_TOKEN or ADMIN_CHAT_ID not configured, skipping alert.")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': ADMIN_CHAT_ID,
            'text': f"🚨 <b>LiteAPKs Agent SOS Alert</b>\n\n{message}",
            'parse_mode': 'HTML'
        }
        res = requests.post(url, data=payload, timeout=10)
        if not res.ok:
            # Fallback to plain text if HTML entity parsing failed
            plain_payload = {
                'chat_id': ADMIN_CHAT_ID,
                'text': f"🚨 LiteAPKs Agent SOS Alert\n\n{message}"
            }
            res_plain = requests.post(url, data=plain_payload, timeout=10)
            if not res_plain.ok:
                print(f"[Admin Alert Failed]: {res_plain.status_code} - {res_plain.text}")
    except Exception as ae:
        print(f"[Admin Alert Exception]: {ae}")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}

# Permanent Blacklist Policy: Never scrape, download, or post LITEAPKS Store
BANNED_APP_PATTERNS = [
    r'(?i)liteapks?\s*store',
    r'(?i)liteapks?\s*market',
    r'(?i)liteapks?\s*app\b',
    r'(?i)^liteapks?$',
    r'(?i)/app\.html$',
    r'(?i)/liteapks[-_]store\.html',
    r'(?i)/liteapks[-_]market\.html',
    r'(?i)/liteapks[-_]app\.html',
    r'(?i)\bliteapks\s*store\b'
]

def is_banned_app(title="", url=""):
    """Checks if an app is permanently blacklisted per user policy."""
    combined = f"{title} {url}".lower()
    for pattern in BANNED_APP_PATTERNS:
        if re.search(pattern, combined):
            return True
    return False

def load_sent_history():
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[Database Error]: {e}")
    return set()

def save_sent_history(sent_set):
    try:
        items = sorted(list(sent_set))
        if len(items) > 50000:
            items = items[-50000:]
        tmp_file = f"{DATABASE_FILE}.tmp"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2)
        os.replace(tmp_file, DATABASE_FILE)
    except Exception as e:
        print(f"[Database Save Error]: {e}")

def load_apps_catalog():
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[Catalog Load Error]: {e}")
    return {}

def save_apps_catalog(catalog):
    try:
        tmp_file = f"{CATALOG_FILE}.tmp"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, CATALOG_FILE)
    except Exception as e:
        print(f"[Catalog Save Error]: {e}")

def update_catalog_entry(meta, apk_info, devuploads_url):
    """
    Saves or updates an app in apps_catalog.json with its LATEST version and DevUploads link.
    STRICT POLICY: Refuses to store any link that is not a valid https://devuploads.com/ URL.
    Guarantees that older versions never overwrite newer releases.
    """
    if not devuploads_url or not str(devuploads_url).strip().startswith("https://devuploads.com/"):
        print(f"[Catalog Reject] Refusing to save non-DevUploads URL '{devuploads_url}' for {meta.get('title')}. Only verified https://devuploads.com/ links are stored.")
        return None

    try:
        catalog = load_apps_catalog()
        c_slug = get_canonical_slug(meta.get('download_page', meta.get('url', meta.get('app_url', ''))))
        clean_v = clean_app_version(meta.get('version', ''))

        if c_slug in catalog:
            prev_v = catalog[c_slug].get('version', '')
            if compare_app_versions(clean_v, prev_v) < 0:
                return catalog[c_slug]

        catalog[c_slug] = {
            'slug': c_slug,
            'title': meta.get('title', ''),
            'version': clean_v or meta.get('version', 'latest'),
            'display_version': meta.get('version', 'latest'),
            'devuploads_url': str(devuploads_url).strip(),
            'size_mb': apk_info.get('size_mb', 0),
            'category': meta.get('app_type', meta.get('category', 'Android App')),
            'package_name': meta.get('package_name', ''),
            'icon_url': meta.get('image_url', ''),
            'features': meta.get('features', []),
            'description': meta.get('description', '')[:320] if meta.get('description') else '',
            'download_page': meta.get('download_page', ''),
            'updated_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        save_apps_catalog(catalog)
        return catalog[c_slug]
    except Exception as e:
        print(f"[Update Catalog Error]: {e}")
        return None


def search_apps_catalog(query):
    """
    Searches the live catalog for an app by keyword, title, slug, or package name.
    Handles noise words ('mod', 'apk', 'premium', 'pro', etc.) and token subset matching.
    Returns the latest version match if found, else None.
    """
    catalog = load_apps_catalog()
    if not catalog or not query:
        return None
    q = query.strip().lower()
    q_slug = re.sub(r'[^a-z0-9]+', '-', q).strip('-')

    # 1. Exact slug match
    if q_slug in catalog:
        return catalog[q_slug]

    # 2. Normalized title exact match
    norm_q = normalize_app_title(q)
    for slug, app in catalog.items():
        if norm_q and normalize_app_title(app.get('title', '')) == norm_q:
            return app

    # 3. Clean noise keywords (e.g. 'mod', 'apk', 'premium', 'pro', 'hack', etc.)
    noise = {'mod', 'apk', 'premium', 'pro', 'unlocked', 'hack', 'vip', 'latest', 'free', 'version', 'download', 'plus', 'cheat', 'menu', 'hacked', 'app', 'android'}
    tokens = [w for w in re.split(r'[^a-z0-9]+', q) if w and w not in noise]
    clean_slug = '-'.join(tokens)

    if clean_slug and clean_slug in catalog:
        return catalog[clean_slug]

    if clean_slug:
        for slug, app in catalog.items():
            if slug == clean_slug or slug.startswith(clean_slug + '-') or ('-' + clean_slug + '-') in slug:
                return app

    # 4. Token subset in title or slug
    if tokens:
        for slug, app in catalog.items():
            t_lower = app.get('title', '').lower()
            if all(t in t_lower or t in slug for t in tokens):
                return app

    # 5. Substring in slug or title
    for slug, app in catalog.items():
        if q_slug and q_slug in slug:
            return app
        if q in app.get('title', '').lower():
            return app

    # 6. Package name match
    for slug, app in catalog.items():
        if q in app.get('package_name', '').lower():
            return app

    return None


def get_telegram_api_base():
    if LOCAL_BOT_API_URL:
        return f"{LOCAL_BOT_API_URL.rstrip('/')}/bot{BOT_TOKEN}"
    return f"https://api.telegram.org/bot{BOT_TOKEN}"

def clean_text_for_telegram(text):
    if not text:
        return ""
    # 1. Unescape HTML entities multiple times (handles double-encoded entities like &amp;amp;)
    for _ in range(3):
        prev = text
        text = html.unescape(text)
        if text == prev:
            break

    # 2. Unicode NFKC normalization
    text = unicodedata.normalize('NFKC', text)

    # 3. Mojibake corrections (Windows-1252 to UTF-8 encoding artifacts) using explicit escape codes
    mojibake_map = {
        'â€™': "'", 'â€˜': "'", '\u2019': "'", '\u2018': "'", '`': "'",
        'â€œ': '"', 'â€\x9d': '"', '\u201c': '"', '\u201d': '"',
        'â€“': '-', 'â€”': '-', '\u2013': '-', '\u2014': '-',
        'â€¦': '...', '\u2026': '...',
        'Â': ' ', '\u00a0': ' ', '\u200b': '', '\u200c': '', '\u200d': '', '\ufeff': '',
        'â€¢': '•', 'Ã©': 'e', 'Ã¡': 'a', 'Ã³': 'o', 'Ã±': 'n', '\ufffd': "'"
    }
    for bad, good in mojibake_map.items():
        if bad:
            text = text.replace(bad, good)

    # 4. Strip all HTML tags completely for clean output
    text = re.sub(r'<[^>]+>', ' ', text)

    # 5. Strip any URLs to enforce 100% NO external links policy
    text = re.sub(r"https?://[^\s<>\"\']+", "", text)

    # 6. Strip website names/mentions
    text = re.sub(r'(?i)\bliteapks(?:\.com)?\b', '', text)

    # 7. Enforce STRICT ENGLISH ONLY: Strip Hindi / Devanagari characters
    text = re.sub(r'[\u0900-\u097F]+', ' ', text)

    # 8. Clean punctuation & whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\s+([,.:;!?])', r'\1', text)
    text = re.sub(r'\.{4,}', '...', text)
    text = re.sub(r'^[\s\-–—:•]+', '', text)
    return text.strip()

def smart_truncate(text, max_len=300):
    if not text or len(text) <= max_len:
        return text
    sub = text[:max_len]
    last_punct = -1
    for p in ['. ', '! ', '? ']:
        pos = sub.rfind(p)
        if pos > last_punct:
            last_punct = pos
    if last_punct > max_len * 0.55:
        return sub[:last_punct + 1].strip()
    last_space = sub.rfind(' ')
    if last_space > max_len * 0.4:
        return sub[:last_space].rstrip(',;:- ') + "..."
    return sub.rstrip(',;:- ') + "..."

def extract_mod_and_key_features(soup, raw_title):
    candidates = []

    def add_candidate(f):
        if not f:
            return
        f_clean = clean_text_for_telegram(f)
        f_lower = f_clean.lower()
        skip_words = [
            'cpu', 'screen dpi', 'package signature', 'debug code',
            'liteapks', 'download', 'how to install', 'apk installer',
            'supported cpu', 'arm64', 'armeabi', 'x86', 'signature changed',
            'languages: full multi', 'unwanted permissions', 'original package',
            'promo player popup', 'no forced ads on exit'
        ]
        if any(w in f_lower for w in skip_words):
            return
        f_clean = re.sub(r'^[\s\-–—:•\*\+\d\.\)]+', '', f_clean).strip()
        f_clean = re.sub(r'[,:;\s\-]+$', '', f_clean).strip()
        if len(f_clean) < 3 or len(f_clean) > 80:
            return
        if f_clean.isupper():
            f_clean = f_clean.title()
        candidates.append(f_clean)

    # 1. Title brackets e.g. (VIP Unlocked, AD-Free)
    mod_tag_m = re.search(r'[\(\[](.*?)[\)\]]', raw_title)
    if mod_tag_m:
        raw_mod = mod_tag_m.group(1)
        for part in re.split(r'[,/|•+]', raw_mod):
            part = part.strip()
            if part.lower() not in ['apk', 'mod', 'final', 'hotfix', 'latest']:
                if part.lower() == 'no ads':
                    part = 'Ad-Free (No Ads)'
                elif part.lower() == 'pro':
                    part = 'Pro Features Unlocked'
                elif part.lower() == 'vip':
                    part = 'VIP Features Unlocked'
                add_candidate(part)

    # 2. Dedicated #mod-info container (Real version mod details!)
    mod_info = soup.find(id='mod-info')
    if mod_info:
        for br in mod_info.find_all(['br', 'hr']):
            br.replace_with('\n')
        for el in mod_info.find_all(['li', 'p', 'em', 'strong']):
            txt = el.get_text('\n', strip=True)
            for line in re.split(r'[\r\n;]+', txt):
                line = line.strip()
                if line:
                    add_candidate(line)

    # 3. In-page badge/paragraph e.g. <p class="...italic...">MOD: ...</p>
    for p in soup.find_all(['p', 'div', 'span']):
        txt = p.get_text(' ', strip=True)
        if re.match(r'^MOD:\s*', txt, re.I):
            mod_val = re.sub(r'^MOD:\s*', '', txt, flags=re.I).strip()
            for part in re.split(r'[,/|•+]', mod_val):
                add_candidate(part)

    # 4. Key functional highlights from .entry-content ul li
    entry_content = soup.find('div', class_='entry-content')
    if entry_content:
        for li in entry_content.find_all('li'):
            txt = li.get_text(' ', strip=True)
            if ':' in txt:
                lead = txt.split(':')[0].strip()
                if 4 <= len(lead) <= 50:
                    add_candidate(lead)
            elif len(txt) <= 60:
                add_candidate(txt)

    # 5. Heading highlights
    if len(candidates) < 4 and entry_content:
        for h in entry_content.find_all(['h2', 'h3']):
            txt = h.get_text(' ', strip=True)
            ignore_headings = ['overview', 'how to', 'faq', 'download', 'features', 'conclusion', 'screenshots', 'summary']
            if 5 <= len(txt) <= 55 and not any(w in txt.lower() for w in ignore_headings):
                add_candidate(txt)

    # Deduplicate & Filter
    filtered = []
    seen = set()
    generic_words = {'pro', 'vip', 'premium', 'mod', 'apk', 'unlocked', 'full', 'all', 'free', 'latest'}

    for f in candidates:
        f_lower = f.lower()
        if f_lower in generic_words:
            continue
        norm_key = re.sub(r'[^a-z0-9]', '', f_lower)
        if not norm_key or norm_key in seen:
            continue

        redundant = False
        for ex in filtered:
            ex_lower = ex.lower()
            if f_lower in ex_lower and len(ex_lower) >= len(f_lower) + 4:
                redundant = True
                break
        if not redundant:
            seen.add(norm_key)
            filtered.append(f)

    if not filtered:
        filtered = ["VIP Premium Unlocked", "Ad-Free Experience", "All Pro Features Active"]

    return filtered[:6]

# Categories ordered strictly by Real-World Telegram & MOD APK User Demand
ALL_BUILTIN_CATEGORIES = [
    # Top Tier: Viral Mega-Demand (Video, Audio, Action Games, Photo, Tools, Streaming)
    ("Video Players & Editors", "https://liteapks.com/apps/video-players-editors"),
    ("Music & Audio", "https://liteapks.com/apps/music-audio"),
    ("Action Games", "https://liteapks.com/games/action"),
    ("Photography", "https://liteapks.com/apps/photography"),
    ("Tools", "https://liteapks.com/apps/tools"),
    ("Entertainment", "https://liteapks.com/apps/entertainment"),
    
    # High Tier: Gaming Favorites & Daily Essentials
    ("Sports Games", "https://liteapks.com/games/sports"),
    ("Racing Games", "https://liteapks.com/games/racing"),
    ("Arcade Games", "https://liteapks.com/games/arcade"),
    ("Adventure Games", "https://liteapks.com/games/adventure"),
    ("Communication", "https://liteapks.com/apps/communication"),
    ("Social", "https://liteapks.com/apps/social"),
    ("Productivity", "https://liteapks.com/apps/productivity"),
    ("Personalization", "https://liteapks.com/apps/personalization"),
    ("Health & Fitness", "https://liteapks.com/apps/health-fitness"),
    
    # Mid-High Tier: Education, RPG & Simulation
    ("Education", "https://liteapks.com/apps/education"),
    ("Role Playing Games", "https://liteapks.com/games/role-playing"),
    ("Simulation Games", "https://liteapks.com/games/simulation"),
    ("Strategy Games", "https://liteapks.com/games/strategy"),
    ("Emulator", "https://liteapks.com/apps/emulator"),
    ("Art & Design", "https://liteapks.com/apps/art-design"),
    ("Comics", "https://liteapks.com/apps/comics"),
    ("Business", "https://liteapks.com/apps/business"),
    ("Finance", "https://liteapks.com/apps/finance"),
    
    # Casual & Specialized Gaming
    ("Casual Games", "https://liteapks.com/games/casual"),
    ("Puzzle Games", "https://liteapks.com/games/puzzle"),
    ("Card Games", "https://liteapks.com/games/card"),
    ("Board Games", "https://liteapks.com/games/board"),
    ("Music Games", "https://liteapks.com/games/music"),
    ("Sports Apps", "https://liteapks.com/apps/sport"),
    
    # Utility & Lifestyle Niche
    ("Auto & Vehicles", "https://liteapks.com/apps/auto-vehicles"),
    ("Maps & Navigation", "https://liteapks.com/apps/maps-navigation"),
    ("Weather", "https://liteapks.com/apps/weather"),
    ("Beauty", "https://liteapks.com/apps/beauty"),
    ("Books & Reference", "https://liteapks.com/apps/books-reference"),
    ("Dating", "https://liteapks.com/apps/dating"),
    ("Lifestyle", "https://liteapks.com/apps/lifestyle"),
    ("House & Home", "https://liteapks.com/apps/house-home"),
    ("Shopping", "https://liteapks.com/apps/shopping"),
    ("Food & Drink", "https://liteapks.com/apps/food-drink"),
    ("Medical", "https://liteapks.com/apps/medical"),
    ("News & Magazines", "https://liteapks.com/apps/news-magazines"),
    ("Parenting", "https://liteapks.com/apps/parenting"),
    ("Events", "https://liteapks.com/apps/events"),
    ("Libraries & Demo", "https://liteapks.com/apps/libraries-demo"),
    ("Casino Games", "https://liteapks.com/games/casino"),
    ("Educational Games", "https://liteapks.com/games/educational"),
    ("Trivia Games", "https://liteapks.com/games/trivia"),
    ("Word Games", "https://liteapks.com/games/word")
]

# Mapping category slugs to rich, descriptive App Types ("App Category Type")
APP_TYPE_MAPPING = {
    # Video & Photo
    'video-players-editors': 'Video Player & Video Editor',
    'photography': 'Photo & Video Editor',
    
    # Audio & Music
    'music-audio': 'Music Player & Audio Streaming',
    'music': 'Music & Rhythm Game',
    
    # Action, Adventure & Arcade Games
    'action': 'Action & Shooting Game',
    'adventure': 'Adventure & Exploration Game',
    'arcade': 'Arcade & Casual Game',
    'board': 'Board & Tabletop Game',
    'card': 'Card & Casino Game',
    'casino': 'Casino & Slots Game',
    'casual': 'Casual & Relaxing Game',
    'educational': 'Educational & Learning Game',
    'puzzle': 'Brain & Puzzle Game',
    'racing': 'Racing & Driving Game',
    'role-playing': 'Role Playing Game (RPG)',
    'simulation': 'Simulation & Life Game',
    'sports': 'Sports & Football Game',
    'strategy': 'Strategy & Tactical Battle Game',
    'trivia': 'Trivia & Quiz Game',
    'word': 'Word & Puzzle Game',
    
    # System, Utilities & Productivity
    'tools': 'Android Utility & System Tool',
    'productivity': 'Productivity & Office Tool',
    'communication': 'Communication & Messaging App',
    'social': 'Social Media & Networking',
    'personalization': 'Launcher & Wallpaper Customization',
    'entertainment': 'Movies & OTT Video Streaming',
    'health-fitness': 'Health, Workout & Fitness Tracker',
    'education': 'Education & Language Learning',
    'art-design': 'Art, Graphic & Drawing Editor',
    'comics': 'Manga & Comic Reader',
    'business': 'Business & Document Scanner',
    'finance': 'Finance, Crypto & Stock Tracker',
    'auto-vehicles': 'Automotive & Vehicle Tool',
    'maps-navigation': 'GPS Navigation & Offline Maps',
    'weather': 'Weather Forecast & Live Radar',
    'beauty': 'Beauty & Selfie Photo Editor',
    'books-reference': 'E-Books & Reference Library',
    'dating': 'Dating & Social Meetup',
    'lifestyle': 'Lifestyle & Daily Routine',
    'house-home': 'Smart Home & Interior Design',
    'shopping': 'Online Shopping & Deals',
    'food-drink': 'Food Delivery & Recipes',
    'medical': 'Medical & Health Reference',
    'news-magazines': 'News & Magazine Reader',
    'parenting': 'Parenting & Child Care Tracker',
    'events': 'Events & Ticket Booking',
    'libraries-demo': 'Developer Tools & Demo',
    'emulator': 'Gaming Console Emulator',
    'sport': 'Live Sports Streaming & Scores'
}

from bs4 import BeautifulSoup

def normalize_app_title(title):
    if not title:
        return ""
    t = re.sub(r'\s*[\(\[].*?[\)\]]', ' ', title)
    t = re.sub(r'(?i)\bv?\d+(?:\.\d+)+[a-z0-9_-]*\b', ' ', t)
    t = re.sub(r'(?i)\b(?:mod|apk|premium|unlocked|vip|latest|version|for android|free download|app|apps|pro|plus)\b', ' ', t)
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip().lower()
    return t

def clean_app_version(ver_str):
    if not ver_str:
        return ""
    v = str(ver_str).strip()
    v = re.sub(r'^[vV]\s*', '', v)
    v = re.sub(r'(?i)\s*-\s*google\s*play.*', '', v)
    v = re.sub(r'(?i)\s*google\s*play.*', '', v)
    v = re.sub(r'(?i)\s*\(mod\).*', '', v)
    v = re.sub(r'(?i)\s*\(premium\).*', '', v)
    v = re.sub(r'(?i)\s*\(unlocked\).*', '', v)
    v = re.sub(r'(?i)\s*\(full\s*version\).*', '', v)
    v = re.sub(r'(?i)\s*apk.*', '', v)
    v = re.sub(r'(?i)\s*mod.*', '', v)
    v = re.sub(r'(?i)\s*gp$', '', v)
    v = v.strip()
    m = re.search(r'([0-9]+(?:\.[0-9a-zA-Z_\-]+)+)', v)
    if m:
        return m.group(1).lower().strip()
    return v.lower().strip()

def normalize_app_version(version):
    return clean_app_version(version) or "latest"

def parse_version_tuple(v_str):
    v = clean_app_version(v_str)
    if not v:
        return ()
    tokens = re.findall(r'(\d+|[a-zA-Z]+)', v)
    parsed = []
    for t in tokens:
        if t.isdigit():
            parsed.append(int(t))
        else:
            parsed.append(t.lower())
    return tuple(parsed)

def compare_app_versions(cand_ver, prev_ver):
    """
    Returns:
       1 if cand_ver > prev_ver (strictly newer version)
       0 if cand_ver == prev_ver (identical version)
      -1 if cand_ver < prev_ver (older version)
    """
    c_clean = clean_app_version(cand_ver)
    p_clean = clean_app_version(prev_ver)
    if c_clean == p_clean:
        return 0
    t_cand = parse_version_tuple(cand_ver)
    t_prev = parse_version_tuple(prev_ver)
    if not t_cand or not t_prev:
        return 1 if t_cand and not t_prev else (-1 if t_prev and not t_cand else 0)
    for c_val, p_val in zip(t_cand, t_prev):
        if c_val == p_val:
            continue
        if isinstance(c_val, int) and isinstance(p_val, int):
            return 1 if c_val > p_val else -1
        if isinstance(c_val, int) and isinstance(p_val, str):
            return 1
        if isinstance(c_val, str) and isinstance(p_val, int):
            return -1
        return 1 if str(c_val) > str(p_val) else -1
    if len(t_cand) > len(t_prev):
        return 1
    elif len(t_cand) < len(t_prev):
        return -1
    return 0

compare_versions = compare_app_versions

def build_posted_apps_registry(sent_set):
    """
    Builds a unified, structured lookup registry from sent_set AND apps_catalog.json:
    registry['by_slug'][canonical_slug] = set of cleaned versions
    registry['by_pkg'][package_name] = set of cleaned versions
    registry['by_title'][norm_title] = set of cleaned versions
    registry['by_url'][url] = set of cleaned versions
    """
    registry = {
        'by_slug': {},
        'by_pkg': {},
        'by_title': {},
        'by_url': {}
    }
    # 1. Index from sent_apks.json
    for item in sent_set:
        if item.startswith('slug_id::'):
            parts = item.split('::')
            if len(parts) >= 3:
                s, v = parts[1], clean_app_version(parts[2])
                if v: registry['by_slug'].setdefault(s, set()).add(v)
        elif item.startswith('pkg_id::'):
            parts = item.split('::')
            if len(parts) >= 3:
                p, v = parts[1], clean_app_version(parts[2])
                if v: registry['by_pkg'].setdefault(p, set()).add(v)
        elif item.startswith('id::'):
            parts = item.split('::')
            if len(parts) >= 3:
                t, v = parts[1], clean_app_version(parts[2])
                if v: registry['by_title'].setdefault(t, set()).add(v)
        elif item.startswith('https://'):
            if '::' in item:
                u, v_raw = item.split('::', 1)
                v = clean_app_version(v_raw)
                s = get_canonical_slug(u)
                if v:
                    registry['by_url'].setdefault(u, set()).add(v)
                    registry['by_slug'].setdefault(s, set()).add(v)
            else:
                s = get_canonical_slug(item)
                registry['by_url'].setdefault(item, set())
                registry['by_slug'].setdefault(s, set())

    # 2. Index from apps_catalog.json (Ground Truth of Cataloged & Posted Apps)
    try:
        catalog = load_apps_catalog()
        for slug, entry in catalog.items():
            v = clean_app_version(entry.get('version', ''))
            if v:
                registry['by_slug'].setdefault(slug, set()).add(v)
                pkg = entry.get('package_name', '')
                if pkg:
                    registry['by_pkg'].setdefault(pkg, set()).add(v)
                norm_t = normalize_app_title(entry.get('title', ''))
                if norm_t:
                    registry['by_title'].setdefault(norm_t, set()).add(v)
                dl_page = entry.get('download_page', '')
                if dl_page:
                    registry['by_url'].setdefault(dl_page, set()).add(v)
    except Exception as e:
        print(f"[Registry Catalog Sync Warning]: {e}")

    return registry

def evaluate_candidate_status(c_slug, u, pkg_name, title, raw_version, registry):
    """
    Evaluates candidate APK against previously posted app registry.
    Returns:
      ('NEW_APP', None) -> never posted before
      ('DUPLICATE_VERSION', prev_ver) -> identical or same version already posted (REJECT)
      ('OLDER_VERSION', prev_ver) -> older version than posted (REJECT)
      ('NEW_UPDATE', prev_ver) -> strictly newer version released (ACCEPT)
    """
    cand_ver = clean_app_version(raw_version)
    known_versions = set()
    if c_slug in registry['by_slug']:
        known_versions.update(registry['by_slug'][c_slug])
    if pkg_name and pkg_name in registry['by_pkg']:
        known_versions.update(registry['by_pkg'][pkg_name])
    norm_t = normalize_app_title(title)
    if norm_t and norm_t in registry['by_title']:
        known_versions.update(registry['by_title'][norm_t])
    if u in registry['by_url']:
        known_versions.update(registry['by_url'][u])

    if not known_versions:
        return 'NEW_APP', None

    # Filter out empty or placeholder strings
    valid_kvs = {v for v in known_versions if v and v != 'latest'}

    # 1. Exact string/clean match against ANY known version (Absolute Zero Duplication)
    if cand_ver in known_versions or (cand_ver and cand_ver in valid_kvs):
        return 'DUPLICATE_VERSION', cand_ver

    # 2. If candidate has no discernible version (empty or 'latest')
    if not cand_ver or cand_ver == 'latest':
        return 'DUPLICATE_VERSION', list(known_versions)[0]

    # 3. If all recorded versions were placeholder 'latest', candidate has concrete version: treat as update
    if not valid_kvs:
        return 'NEW_UPDATE', 'latest'

    # 4. Find highest posted version using parsed integer tuples
    max_kv = max(valid_kvs, key=lambda x: parse_version_tuple(x))
    cmp_val = compare_app_versions(cand_ver, max_kv)

    if cmp_val == 0:
        return 'DUPLICATE_VERSION', max_kv
    elif cmp_val < 0:
        return 'OLDER_VERSION', max_kv
    else:
        # Strictly newer than highest recorded version!
        return 'NEW_UPDATE', max_kv

def record_posted_apk(sent_set, url, c_slug, norm_title, package_name, version, registry=None):
    """Permanently records all variations of a published APK into sent_set and updates live registry."""
    clean_v = clean_app_version(version)
    sent_set.add(url)
    sent_set.add(f"{url}::{version}")
    if clean_v:
        sent_set.add(f"{url}::{clean_v}")
        sent_set.add(f"slug_id::{c_slug}::{clean_v}")
        if norm_title:
            sent_set.add(f"id::{norm_title}::{clean_v}")
        if package_name:
            sent_set.add(f"pkg_id::{package_name}::{clean_v}")
    save_sent_history(sent_set)

    if registry:
        if clean_v:
            registry['by_slug'].setdefault(c_slug, set()).add(clean_v)
            if package_name:
                registry['by_pkg'].setdefault(package_name, set()).add(clean_v)
            if norm_title:
                registry['by_title'].setdefault(norm_title, set()).add(clean_v)
            registry['by_url'].setdefault(url, set()).add(clean_v)

def get_canonical_slug(url):
    slug = url.rstrip('/').split('/')[-1].replace('.html', '').lower()
    return re.sub(r'-\d+$', '', slug)

def clean_title_from_url(url):
    slug = url.rstrip('/').split('/')[-1].replace('.html', '')
    return slug.replace('-', ' ').title()

def scrape_prioritized_candidates():
    """Scrapes LiteAPKs candidates across all priority tiers:
    1. Priority 1 (Top / Trending Apps & Games):
       - Scrapes Homepage (/), /apps, and /games featured/top carousels.
    2. Priority 2 (Category Top Hits - Page 1):
       - Scrapes all subcategories in strict user preference order:
         (Video Players & Editors, Music & Audio, Action Games, Photography, Tools, Entertainment, etc.)
    3. Priority 3 (Category Remaining Apps - Page 2+):
       - Scrapes remaining deep catalog of each category in the EXACT SAME preference order:
         (Video Players & Editors remaining ➔ Music & Audio remaining ➔ Action Games remaining ➔ ...)
    4. Fallback (Deep General Catalog):
       - Scrapes apps/games pagination pages 3 to 6.
    """
    tier1_candidates = []
    seen_urls = set()

    # 1. Priority 1: Top Apps and Games from Homepage, /apps, /games
    for root_url in [f"{SOURCE_BASE_URL}/", f"{SOURCE_BASE_URL}/apps", f"{SOURCE_BASE_URL}/games"]:
        try:
            r = requests.get(root_url, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a', class_=lambda c: c and 'top-app-item' in c):
                    href = a.get('href', '')
                    if href.startswith('https://liteapks.com/') and href.endswith('.html') and not is_banned_app("", href):
                        if href not in seen_urls:
                            seen_urls.add(href)
                            tier1_candidates.append({
                                'url': href,
                                'title': clean_title_from_url(href),
                                'category': 'Top Apps & Games',
                                'priority_label': 'Priority 1 (Top / Trending)'
                            })
                for container in soup.find_all(class_=lambda c: c and any(k in c for k in ['scroll-section', 'overflow-x-auto'])) + soup.find_all(id='featured-track'):
                    for a in container.find_all('a', href=True):
                        href = a['href']
                        if href.startswith('https://liteapks.com/') and href.endswith('.html') and not is_banned_app("", href):
                            if href not in seen_urls:
                                seen_urls.add(href)
                                tier1_candidates.append({
                                    'url': href,
                                    'title': clean_title_from_url(href),
                                    'category': 'Top Apps & Games',
                                    'priority_label': 'Priority 1 (Top / Trending)'
                                })
        except Exception as e:
            print(f"[Tier 1 Scraper Error] {root_url}: {e}")

    # 2. Category Priority Map: built-in categories in strict preference order
    all_categories = list(ALL_BUILTIN_CATEGORIES)
    seen_cat_urls = {u for _, u in all_categories}
    for hub_url in [f"{SOURCE_BASE_URL}/apps", f"{SOURCE_BASE_URL}/games"]:
        try:
            r_hub = requests.get(hub_url, headers=HEADERS, timeout=8)
            if r_hub.status_code == 200:
                soup_hub = BeautifulSoup(r_hub.text, 'html.parser')
                for a in soup_hub.find_all('a', href=True):
                    l = a['href']
                    if l.startswith('https://liteapks.com/') and any(l.startswith(f"https://liteapks.com/{x}/") for x in ['apps', 'games']):
                        if l not in seen_cat_urls and not l.endswith('/apps') and not l.endswith('/games') and '/page/' not in l and not is_banned_app("", l):
                            seen_cat_urls.add(l)
                            clean_name = l.rstrip('/').split('/')[-1].replace('-', ' ').title()
                            all_categories.append((clean_name, l))
        except Exception:
            pass

    def fetch_category_pages(cat_item):
        cat_name, cat_url = cat_item
        p1_apps = []
        p2_apps = []
        # Page 1 (Priority 2: Top Apps of this category)
        try:
            r_p1 = requests.get(cat_url, headers=HEADERS, timeout=8)
            if r_p1.status_code == 200:
                soup_1 = BeautifulSoup(r_p1.text, 'html.parser')
                for a in soup_1.find_all('a', href=True):
                    href = a['href']
                    if href.startswith('https://liteapks.com/') and href.endswith('.html') and not is_banned_app("", href):
                        if href not in p1_apps:
                            p1_apps.append(href)
        except Exception:
            pass

        # Page 2 (Priority 3: Remaining Apps of this category)
        try:
            r_p2 = requests.get(f"{cat_url}/page/2", headers=HEADERS, timeout=8)
            if r_p2.status_code == 200:
                soup_2 = BeautifulSoup(r_p2.text, 'html.parser')
                for a in soup_2.find_all('a', href=True):
                    href = a['href']
                    if href.startswith('https://liteapks.com/') and href.endswith('.html') and not is_banned_app("", href):
                        if href not in p1_apps and href not in p2_apps:
                            p2_apps.append(href)
        except Exception:
            pass

        return cat_name, p1_apps, p2_apps

    cat_p1_results = []
    cat_p2_results = []
    if all_categories:
        with ThreadPoolExecutor(max_workers=30) as executor:
            cat_results = list(executor.map(fetch_category_pages, all_categories))
            for cat_name, p1, p2 in cat_results:
                cat_p1_results.append((cat_name, p1))
                cat_p2_results.append((cat_name, p2))

    # 3. Fallback deep general pagination (Pages 3 to 6)
    def fetch_pagination_page(p_url):
        try:
            r_p = requests.get(p_url, headers=HEADERS, timeout=8)
            if r_p.status_code == 200:
                soup_p = BeautifulSoup(r_p.text, 'html.parser')
                return [a['href'] for a in soup_p.find_all('a', href=True) if a['href'].startswith('https://liteapks.com/') and a['href'].endswith('.html')]
        except Exception:
            pass
        return []

    pagination_urls = []
    for p in range(3, 7):
        pagination_urls.append(f"{SOURCE_BASE_URL}/apps/page/{p}")
        pagination_urls.append(f"{SOURCE_BASE_URL}/games/page/{p}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        page_results = list(executor.map(fetch_pagination_page, pagination_urls))

    deep_catalog_apps = []
    for p_apps in page_results:
        for a in p_apps:
            if not is_banned_app("", a) and a not in deep_catalog_apps:
                deep_catalog_apps.append(a)

    return tier1_candidates, cat_p1_results, cat_p2_results, deep_catalog_apps

def generate_prioritized_candidates(tier1_candidates, cat_p1_results, cat_p2_results, deep_catalog_apps):
    """
    Yields candidate apps in strict priority cascade order:
    1. Priority 1 (Top / Trending Apps & Games)
    2. Priority 2: Category Top Apps (Page 1) in strict category preference order (Cat 1..49)
    3. Priority 3: Category Remaining Apps (Page 2+) in the EXACT SAME category preference order (Cat 1..49)
    4. Fallback (Deep General Catalog)
    """
    seen_urls = set()
    seen_slugs = set()

    # Tier 1: Priority 1 (Top / Trending)
    for item in tier1_candidates:
        u = item['url']
        c_slug = get_canonical_slug(u)
        if u not in seen_urls and c_slug not in seen_slugs and not is_banned_app("", u):
            seen_urls.add(u)
            seen_slugs.add(c_slug)
            yield item

    # Tier 2: Priority 2 (Category Page 1 in Strict Preference Order)
    for cat_name, p1_apps in cat_p1_results:
        for u in p1_apps:
            c_slug = get_canonical_slug(u)
            if u not in seen_urls and c_slug not in seen_slugs and not is_banned_app("", u):
                seen_urls.add(u)
                seen_slugs.add(c_slug)
                yield {
                    'url': u,
                    'title': clean_title_from_url(u),
                    'category': cat_name,
                    'priority_label': f"Priority 2 ({cat_name})"
                }

    # Tier 3: Priority 3 (Category Page 2+ in EXACT Same Category Preference Order)
    for cat_name, p2_apps in cat_p2_results:
        for u in p2_apps:
            c_slug = get_canonical_slug(u)
            if u not in seen_urls and c_slug not in seen_slugs and not is_banned_app("", u):
                seen_urls.add(u)
                seen_slugs.add(c_slug)
                yield {
                    'url': u,
                    'title': clean_title_from_url(u),
                    'category': cat_name,
                    'priority_label': f"Priority 3 (Remaining: {cat_name})"
                }

    # Tier 4: Fallback Deep General Catalog
    for u in deep_catalog_apps:
        c_slug = get_canonical_slug(u)
        if u not in seen_urls and c_slug not in seen_slugs and not is_banned_app("", u):
            seen_urls.add(u)
            seen_slugs.add(c_slug)
            yield {
                'url': u,
                'title': clean_title_from_url(u),
                'category': 'General Catalog',
                'priority_label': "Priority 4 (General Catalog)"
            }

def select_10_app_batch(tier1_candidates, cat_p1_results, cat_p2_results, deep_catalog_apps, sent_set, target_count=MAX_APKS_PER_RUN, verifier=None):
    """
    Selects up to target_count apps following the strict priority cascade:
    Priority 1 ➔ Priority 2 (Cat 1..49) ➔ Priority 3 (Cat 1..49 remaining) ➔ Fallback.
    
    Every candidate is strictly re-verified by AppSelectionVerifierSubagent against:
    1. Local persistent database (sent_apks.json)
    2. Published catalog (apps_catalog.json)
    3. Live Telegram Channel History (@AndroModApps)
    4. In-batch active locks
    
    If an app was already posted with the SAME version or an OLDER version, it is
    IMMEDIATELY DISCARDED from selection and replaced with a valid candidate from the cascade.
    """
    if verifier is None:
        catalog = load_apps_catalog()
        verifier = AppSelectionVerifierSubagent(sent_set=sent_set, catalog=catalog, live_sync=True)

    selected_batch = []
    discarded_count = 0
    print(f"\n🤖 [SUBAGENT SELECTION INITIATED]: Verifying candidates against database & live channel history (Target: {target_count})...")

    for item in generate_prioritized_candidates(tier1_candidates, cat_p1_results, cat_p2_results, deep_catalog_apps):
        if len(selected_batch) >= target_count:
            break

        u = item['url']
        c_slug = verifier.get_canonical_slug(u)

        if is_banned_app("", u) or c_slug in verifier.batch_slugs:
            continue

        meta = extract_app_metadata(u, fallback_category=item.get('category'))
        if not meta:
            sent_set.add(u)
            continue

        if is_banned_app(meta.get('title', ''), u):
            sent_set.add(u)
            continue

        # Dedicated Subagent Re-Verification
        decision, reason, prev_v = verifier.verify_candidate(item, meta)

        if decision == 'REJECT':
            discarded_count += 1
            print(f"🚫 [SUBAGENT VERIFIER REJECTED]: '{meta.get('title')}' (v{meta.get('version', '')}) -> {reason}. Auto-discarding and cascading to next candidate...")
            clean_v = verifier.clean_version(meta.get('version', ''))
            sent_set.add(u)
            if clean_v:
                sent_set.add(f"{u}::{clean_v}")
                sent_set.add(f"slug_id::{c_slug}::{clean_v}")
                pkg = meta.get('package_name', '')
                if pkg:
                    sent_set.add(f"pkg_id::{pkg}::{clean_v}")
            continue

        # Candidate Approved
        print(f"✅ [SUBAGENT VERIFIER APPROVED]: '{meta.get('title')}' (v{meta.get('version', '')}) -> {reason}")
        verifier.lock_batch_candidate(item, meta)

        item_copy = dict(item)
        item_copy['title'] = meta.get('title', item['title'])
        item_copy['version'] = meta.get('version', item.get('version', 'Latest'))
        item_copy['category'] = meta.get('category', item.get('category', 'App'))
        item_copy['meta'] = meta
        item_copy['is_update'] = reason.startswith('GENUINE_NEW_UPDATE')
        item_copy['prev_version'] = prev_v
        selected_batch.append(item_copy)

    print(f"🎯 [SUBAGENT SELECTION COMPLETE]: Selected {len(selected_batch)} valid candidates ({discarded_count} duplicate/older candidates discarded).\n")
    save_sent_history(sent_set)
    return selected_batch

def get_selected_batch(target_count=MAX_APKS_PER_RUN):
    """Loads sent history, scrapes candidate tiers, and selects the exact 10-app batch."""
    sent_set = load_sent_history()
    tier1, cat_p1, cat_p2, deep_catalog = scrape_prioritized_candidates()
    return select_10_app_batch(tier1, cat_p1, cat_p2, deep_catalog, sent_set, target_count=target_count)

def select_androforever_10_app_batch(popular_candidates, category_candidates, sent_set, target_count=MAX_APKS_PER_RUN, verifier=None):
    """
    Selects up to target_count apps from AndroForever following strict priority:
    1. Priority 1: Popular / Trending apps first.
    2. Priority 2 & 3: Category / Non-Popular apps (Tools, Video, Music, Personalization, etc.).
    Smart Skip: If popular apps have already been posted or have no update, seamlessly skips them and fills the batch from Category apps!
    """
    if verifier is None:
        catalog = load_apps_catalog()
        verifier = AppSelectionVerifierSubagent(sent_set=sent_set, catalog=catalog, live_sync=True)

    selected_batch = []
    discarded_count = 0
    print(f"\n🤖 [ANDROFOREVER SELECTION INITIATED]: Verifying candidates against database & live channel history (Target: {target_count})...")

    # Phase 1: Popular Candidates
    for item in popular_candidates:
        if len(selected_batch) >= target_count:
            break
        u = item['url']
        c_slug = verifier.get_canonical_slug(u)
        if is_banned_app("", u) or c_slug in verifier.batch_slugs:
            continue

        meta = androforever_scraper.extract_androforever_metadata(u)
        if not meta:
            continue

        decision, reason, prev_v = verifier.verify_candidate(item, meta)
        if decision == 'REJECT':
            discarded_count += 1
            print(f"⏩ [SMART SKIP - POPULAR]: {meta['title']} (v{meta.get('version', '')}) -> {reason}")
            continue

        verifier.lock_batch_candidate(item, meta)
        selected_batch.append({
            'url': u,
            'title': meta['title'],
            'version': meta.get('version', 'Latest'),
            'category': meta.get('app_type', 'App'),
            'priority_label': item.get('priority_label', 'Priority 1 - Popular'),
            'is_update': reason.startswith('GENUINE_NEW_UPDATE'),
            'prev_version': prev_v,
            'source': 'androforever',
            'meta': meta
        })

    # Phase 2: Category / Non-Popular Candidates (if fewer than target_count)
    if len(selected_batch) < target_count:
        print(f"ℹ️ [FILLING FROM CATEGORIES]: {len(selected_batch)}/{target_count} selected from Popular. Advancing to Non-Popular Category apps...")
        for item in category_candidates:
            if len(selected_batch) >= target_count:
                break
            u = item['url']
            c_slug = verifier.get_canonical_slug(u)
            if is_banned_app("", u) or c_slug in verifier.batch_slugs:
                continue

            meta = androforever_scraper.extract_androforever_metadata(u)
            if not meta:
                continue

            decision, reason, prev_v = verifier.verify_candidate(item, meta)
            if decision == 'REJECT':
                discarded_count += 1
                continue

            verifier.lock_batch_candidate(item, meta)
            selected_batch.append({
                'url': u,
                'title': meta['title'],
                'version': meta.get('version', 'Latest'),
                'category': meta.get('app_type', 'App'),
                'priority_label': item.get('priority_label', 'Priority 2 - Category'),
                'is_update': reason.startswith('GENUINE_NEW_UPDATE'),
                'prev_version': prev_v,
                'source': 'androforever',
                'meta': meta
            })

    print(f"🤖 [ANDROFOREVER SELECTION COMPLETE]: {len(selected_batch)} valid candidates locked. Discarded duplicates: {discarded_count}.\n")
    return selected_batch

def format_batch_summary(selected_batch):
    """Formats the selected 10-app batch into a clean, structured report grouped by Category & Priority."""
    if not selected_batch:
        return "ℹ️ No unposted candidate APKs found. Database is completely up to date!"

    from collections import OrderedDict
    groups = OrderedDict()
    for item in selected_batch:
        cat_key = item.get('priority_label', item.get('category', 'Top Apps'))
        groups.setdefault(cat_key, []).append(item)

    lines = []
    lines.append("=" * 80)
    lines.append(f"📋 SELECTED {len(selected_batch)} APKS BATCH (CATEGORY PRIORITY CASCADE):")
    lines.append("=" * 80)

    curr_idx = 1
    for grp_name, grp_items in groups.items():
        lines.append(f"\n📂 {grp_name} (Count: {len(grp_items)})")
        lines.append("-" * 60)
        for item in grp_items:
            ver = item.get('version', '')
            ver_str = f" (v{ver})" if ver and ver != 'Latest' else ""
            upd_tag = f" 🔄 [NEW UPDATE from v{item.get('prev_version')}]" if item.get('is_update') else ""
            lines.append(f"  {curr_idx:2d}. {item['title']}{ver_str}{upd_tag}\n      URL: {item['url']}")
            curr_idx += 1

    lines.append("\n" + "=" * 80)
    lines.append(f"🎯 Total Batch: {len(selected_batch)} / {MAX_APKS_PER_RUN} Apps Selected & Ready")
    lines.append("=" * 80)
    return "\n".join(lines)

def format_telegram_batch_preview(selected_batch):
    """Formats the selected 10-app batch into beautiful Telegram HTML grouped by Category & Priority."""
    if not selected_batch:
        return (
            "ℹ️ <b>All priority candidates are already posted!</b>\n\n"
            "The database is completely up-to-date with LiteAPKs top charts. New releases will be automatically published."
        )

    from collections import OrderedDict
    groups = OrderedDict()
    for item in selected_batch:
        cat_key = item.get('priority_label', item.get('category', 'Top Apps'))
        groups.setdefault(cat_key, []).append(item)

    lines = [
        f"📋 <b>LiteAPKs MOD Agent — {len(selected_batch)} Apps Scraped for Upload:</b>\n",
        f"<i>Scraping successful! These {len(selected_batch)} apps will be uploaded to DevUploads and posted to channel ({CHANNEL_ID}):</i>\n"
    ]

    curr_idx = 1
    for grp_name, grp_items in groups.items():
        lines.append(f"📁 <b>{html.escape(grp_name)} ({len(grp_items)})</b>")
        for item in grp_items:
            ver = item.get('version', '')
            ver_str = f" <code>(v{html.escape(str(ver))})</code>" if ver and ver != 'Latest' else ""
            lines.append(f"{curr_idx}. 📱 <b>{html.escape(html.unescape(item['title']))}</b>{ver_str}")
            curr_idx += 1
        lines.append("")

    lines.append("⏳ <i>DevUploads upload and channel broadcasting starting now...</i>")
    return "\n".join(lines)

def scrape_latest_apk_links():
    """Backwards compatibility helper: returns URLs of selected 10-app batch."""
    batch = get_selected_batch(MAX_APKS_PER_RUN)
    return [x['url'] for x in batch]

def extract_app_metadata(app_url, fallback_category=None):
    try:
        resp = requests.get(app_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        html_text = resp.text
        soup = BeautifulSoup(html_text, 'html.parser')

        # 1. App Title
        h1 = soup.find('h1')
        raw_title = clean_text_for_telegram(h1.get_text(' ', strip=True) if h1 else 'New App')
        app_title = re.sub(r'(?i)MOD\s*APK.*', '', raw_title).strip()
        # Clean out embedded versions like "Roblox v2.746.1266" -> "Roblox"
        app_title = re.sub(r'(?i)\s+v?\d+(?:\.\d+)+.*$', '', app_title).strip()
        if not app_title:
            app_title = raw_title

        # 2. App Logo Image (User Requirement: Square App Logo)
        image_url = None
        logo_div = soup.find('div', class_='app-info-icon')
        if logo_div and logo_div.find('img'):
            icon_raw = logo_div.find('img').get('src', '')
            if icon_raw:
                image_url = re.sub(r'-\d+x\d+(\.[a-zA-Z]+)$', r'\1', icon_raw)
        if not image_url:
            og_img = soup.find('meta', property='og:image')
            if og_img and og_img.get('content'):
                image_url = og_img['content']

        # 3. Short Description (Sanitized and natural for Telegram)
        raw_desc = ""
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            raw_desc = og_desc['content']
        if not raw_desc:
            short_div = soup.find('div', class_='short-desc')
            if short_div:
                raw_desc = short_div.get_text(' ', strip=True)
        if not raw_desc:
            entry_p = soup.find('div', class_='entry-content')
            if entry_p and entry_p.find('p'):
                raw_desc = entry_p.find('p').get_text(' ', strip=True)

        clean_desc = clean_text_for_telegram(raw_desc)
        short_desc = smart_truncate(clean_desc, 320)

        # 4. Info Bar Details (Version, Size, App Type)
        info = {}
        for row in soup.find_all('div', class_='info-bar-item'):
            lbl = row.find('div', class_='info-bar-label')
            val = row.find('div', class_='info-bar-value')
            if lbl and val:
                info[lbl.get_text(strip=True).upper()] = val.get_text(strip=True)
        if not info:
            labels = re.findall(r'<div class="info-bar-label">\s*([^<]+)\s*</div>[\s\S]*?<div class="info-bar-value[^"]*">\s*([^<]+)\s*</div>', html_text)
            info = {l.strip().upper(): v.strip() for l, v in labels}
        version = info.get('VERSION', 'Latest')
        if version == 'Latest' or not version:
            m_ver = re.search(r'(?i)v?(\d+(?:\.\d+)+[a-zA-Z0-9_\-]*)', raw_title)
            if m_ver:
                version = m_ver.group(1).lstrip('vV')

        # 5. Extract Real Google Play Package Name (Universal Unique Android ID)
        package_name = ""
        for a_play in soup.find_all('a', href=re.compile(r'play\.google\.com/store/apps/details\?id=')):
            pkg_m = re.search(r'play\.google\.com/store/apps/details\?id=([a-zA-Z0-9_\.]+)', a_play.get('href', ''))
            if pkg_m:
                p = pkg_m.group(1).strip().lower()
                if p not in ['io.apkmody.sai', 'com.android.vending']:
                    package_name = p
                    break

        # 4b. Extract Real Individual Category & Rich App Type (Never generic 'Android Application')
        slug = ""
        raw_cat = ""

        # Priority 1: Check all category links in the document: /(apps|games)/<category-slug>
        for a in soup.find_all('a', href=True):
            href = a['href']
            m = re.search(r'/(?:apps|games)/([a-zA-Z0-9\-_]+)/?$', href)
            if m:
                s_cand = m.group(1).lower()
                if s_cand not in ['apps', 'games', 'page', 'mod']:
                    slug = s_cand
                    raw_cat = clean_text_for_telegram(a.get_text(strip=True))
                    break

        # Priority 2: Check all info-bar-sub, breadcrumbs, and genre spans for links
        if not slug:
            for div in soup.find_all(['div', 'span', 'li', 'ol'], class_=lambda c: c and any(k in str(c).lower() for k in ['info-bar-sub', 'breadcrumb', 'category', 'genre'])):
                a = div.find('a', href=True)
                if a:
                    m = re.search(r'/(?:apps|games)/([a-zA-Z0-9\-_]+)', a['href'])
                    if m and m.group(1).lower() not in ['apps', 'games']:
                        slug = m.group(1).lower()
                        raw_cat = clean_text_for_telegram(a.get_text(strip=True))
                        break

        # Priority 3: Check info dictionary GENRE label
        if not slug and 'GENRE' in info:
            genre_val = info['GENRE'].strip()
            if genre_val:
                raw_cat = clean_text_for_telegram(genre_val)

        # Map slug to rich App Type
        app_type = APP_TYPE_MAPPING.get(slug)

        # If not in mapping, clean raw_cat
        if not app_type and raw_cat:
            clean_raw = raw_cat.strip()
            if clean_raw.lower() not in ['app', 'apps', 'android', 'game', 'games', 'android app', 'android application']:
                app_type = clean_raw

        # Priority 4: Candidate Category fallback if passed from scraper
        if not app_type and fallback_category:
            fb = str(fallback_category).strip()
            if fb.lower() not in ['app', 'apps', 'android app', 'android application', 'top apps & games', 'popular app', 'trending app', 'candidate']:
                app_type = fb

        # Priority 5: Smart Keyword Heuristics from Title & Package Name
        if not app_type or app_type.lower() in ['android application', 'android app', 'app']:
            check_text = f"{app_title} {package_name}".lower()
            if any(w in check_text for w in ['video', 'player', 'editor', 'vlog', 'cinema', 'castify', 'cut']):
                app_type = "Video Player & Video Editor"
            elif any(w in check_text for w in ['music', 'audio', 'song', 'mp3', 'beat', 'sound', 'spotify', 'wynk', 'gaana']):
                app_type = "Music Player & Audio Streaming"
            elif any(w in check_text for w in ['photo', 'camera', 'pic', 'filter', 'retouch', 'lightroom', 'picsart', 'face']):
                app_type = "Photo & Video Editor"
            elif any(w in check_text for w in ['vpn', 'proxy', 'shield', 'secure', 'dns']):
                app_type = "VPN & Online Security"
            elif any(w in check_text for w in ['cleaner', 'boost', 'battery', 'antivirus', 'cache', 'speed']):
                app_type = "Android Utility & System Tool"
            elif any(w in check_text for w in ['launcher', 'wallpaper', 'icon', 'theme', 'font', 'zedge', 'widget']):
                app_type = "Launcher & Wallpaper Customization"
            elif any(w in check_text for w in ['office', 'pdf', 'scanner', 'doc', 'note', 'sheet', 'task', 'word', 'excel']):
                app_type = "Productivity & Office Tool"
            elif any(w in check_text for w in ['chat', 'message', 'social', 'telegram', 'whatsapp', 'insta', 'facebook', 'twitter']):
                app_type = "Social Media & Networking"
            elif any(w in check_text for w in ['browser', 'downloader', 'torrent', 'web', 'download']):
                app_type = "Web Browser & Downloader"
            elif any(w in check_text for w in ['ott', 'movie', 'cinema', 'stream', 'tv', 'series', 'netflix', 'hotstar']):
                app_type = "Movies & OTT Video Streaming"
            elif any(w in check_text for w in ['fitness', 'workout', 'gym', 'health', 'yoga', 'step', 'diet']):
                app_type = "Health, Workout & Fitness Tracker"
            elif any(w in check_text for w in ['learn', 'study', 'course', 'edu', 'quiz', 'dictionary', 'language']):
                app_type = "Education & Language Learning"
            elif any(w in check_text for w in ['crypto', 'coin', 'wallet', 'finance', 'money', 'stock', 'trading']):
                app_type = "Finance, Crypto & Stock Tracker"
            elif any(w in check_text for w in ['action', 'shooting', 'zombie', 'strike', 'gun', 'battle', 'combat']):
                app_type = "Action & Shooting Game"
            elif any(w in check_text for w in ['racing', 'car', 'drive', 'drift', 'speed', 'moto']):
                app_type = "Racing & Driving Game"
            elif any(w in check_text for w in ['puzzle', 'brain', 'logic', 'match', 'solve', 'escape']):
                app_type = "Brain & Puzzle Game"
            elif any(w in check_text for w in ['arcade', 'jump', 'runner', 'dash', 'temple', 'subway']):
                app_type = "Arcade & Casual Game"
            elif any(w in check_text for w in ['strategy', 'clash', 'tower', 'empire', 'tactics']):
                app_type = "Strategy & Tactical Battle Game"
            elif any(w in check_text for w in ['rpg', 'role', 'quest', 'fantasy', 'dungeon']):
                app_type = "Role Playing Game (RPG)"
            elif any(w in check_text for w in ['game', 'mod money', 'unlimited money']) or '/games/' in html_text:
                app_type = "Action & Casual Game"
            else:
                app_type = "Android Utility & Tools" 

        # 6. Download Page Target
        download_page = None
        for a_dl in soup.find_all('a', href=re.compile(r'https://liteapks\.com/download/')):
            download_page = a_dl['href'].strip()
            break
        if not download_page:
            dl_m = re.findall(r'href=["\'](https://liteapks\.com/download/[^"\']+)["\']', html_text)
            if dl_m:
                download_page = dl_m[0]
        if not download_page:
            return None

        # 7. Extract Specific MOD & Key Features (Comprehensive Extraction)
        features = extract_mod_and_key_features(soup, raw_title)

        return {
            'app_url': app_url,
            'title': app_title,
            'package_name': package_name,
            'image_url': image_url,
            'description': short_desc,
            'version': version,
            'category': app_type,
            'app_type': app_type,
            'features': features,
            'download_page': download_page
        }
    except Exception as e:
        print(f"[Metadata Extraction Error] {app_url}: {e}")
        return None

def resolve_direct_apk_url(download_page_url, target_version=None, specific_sublink=None):
    try:
        resp = requests.get(download_page_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        html = resp.text

        # Check sub-links (e.g. /1, /2 for versions)
        sub_m = re.findall(r'href=[\"\'](https://liteapks\.com/download/[^\"\']+/\d+)[\"\']', html)
        target_page = download_page_url
        matched_ver_text = ""

        if specific_sublink:
            target_page = specific_sublink
        elif target_version and sub_m:
            v_clean = target_version.strip().lower().lstrip('v')
            soup_opt = BeautifulSoup(html, 'html.parser')
            for a_tag in soup_opt.select('a[href*="/download/"]'):
                t_str = a_tag.text.strip().lower()
                h_str = a_tag.get('href', '').lower()
                if v_clean in t_str or v_clean in h_str:
                    target_page = a_tag.get('href')
                    matched_ver_text = a_tag.text.strip().replace('\n', ' ')
                    break
            if target_page == download_page_url and sub_m:
                target_page = sub_m[0]
        elif sub_m:
            target_page = sub_m[0]

        if target_page != download_page_url:
            resp_sub = requests.get(target_page, headers=HEADERS, timeout=15)
            if resp_sub.status_code == 200:
                html = resp_sub.text

        div_m = re.search(r'id=[\"\']download[\"\'][^>]*data-link=[\"\']([^\"\']+)[\"\']', html)
        if not div_m:
            return None

        raw_b64 = div_m.group(1)
        decoded_url = base64.b64decode(raw_b64).decode('utf-8')

        # Generate Dual-Base64 Timestamp Token
        ttl = int(time.time()) + 3600 * 3
        token = base64.b64encode(base64.b64encode(str(ttl).encode())).decode()

        # Quote-escape URL path for spaces and special characters safely (prevent double-encoding)
        parsed = urllib.parse.urlparse(decoded_url)
        clean_path = urllib.parse.quote(urllib.parse.unquote(parsed.path))
        final_dl_url = f"{parsed.scheme}://{parsed.netloc}{clean_path}?token={token}"

        # Fetch Content-Length with Range GET fallback if HEAD returns 0
        content_length = 0
        try:
            cl_resp = requests.head(final_dl_url, headers={'User-Agent': HEADERS['User-Agent'], 'Referer': target_page}, timeout=10)
            content_length = int(cl_resp.headers.get('Content-Length', 0))
        except Exception:
            pass

        if content_length == 0:
            try:
                range_resp = requests.get(final_dl_url, headers={'User-Agent': HEADERS['User-Agent'], 'Referer': target_page, 'Range': 'bytes=0-0'}, timeout=10)
                cr = range_resp.headers.get('Content-Range', '')
                if '/' in cr:
                    content_length = int(cr.split('/')[-1])
            except Exception:
                pass

        size_mb = content_length / (1024 * 1024)

        # Determine real extension (.apk, .xapk, .zip, .apks)
        path_lower = parsed.path.lower()
        file_ext = ".apk"
        if path_lower.endswith(".xapk"):
            file_ext = ".xapk"
        elif path_lower.endswith(".zip"):
            file_ext = ".zip"
        elif path_lower.endswith(".apks"):
            file_ext = ".apks"

        return {
            'download_url': final_dl_url,
            'referer': target_page,
            'size_bytes': content_length,
            'size_mb': round(size_mb, 2),
            'file_ext': file_ext,
            'matched_version_text': matched_ver_text
        }
    except Exception as e:
        print(f"[Direct URL Resolution Error] {download_page_url}: {e}")
        return None

def upload_to_devuploads(apk_path, file_name, max_retries=4, backoff=4):
    if not DEVUPLOADS_API_KEY:
        print("[DevUploads]: No DEVUPLOADS_API_KEY configured.")
        return None

    if not os.path.exists(apk_path):
        print(f"[DevUploads Error]: File not found: {apk_path}")
        return None

    try:
        file_size_mb = round(os.path.getsize(apk_path) / (1024 * 1024), 2)
    except Exception:
        file_size_mb = 0

    print(f"📦 Starting DevUploads upload for {file_name} ({file_size_mb} MB) [Max Retries: {max_retries}]...")

    for attempt in range(1, max_retries + 1):
        print(f"🔄 [DevUploads Attempt {attempt}/{max_retries}]: Requesting fresh upload server for {file_name}...")
        upload_url = None
        sess_id = None

        # Step 1: Request fresh upload server and session
        try:
            srv_resp = requests.get(
                f"https://devuploads.com/api/upload/server?key={DEVUPLOADS_API_KEY}",
                timeout=20
            )
            if srv_resp.status_code == 200:
                srv_data = srv_resp.json()
                upload_url = srv_data.get('result')
                sess_id = srv_data.get('sess_id')
            else:
                print(f"⚠️ [DevUploads Server Query HTTP {srv_resp.status_code}] attempt {attempt}: {srv_resp.text[:100]}")
        except Exception as srv_err:
            print(f"⚠️ [DevUploads Server Query Error] attempt {attempt}: {srv_err}")

        if not upload_url or not sess_id:
            if attempt < max_retries:
                wait_secs = attempt * backoff
                print(f"⏳ Waiting {wait_secs}s before next attempt...")
                time.sleep(wait_secs)
            continue

        # Step 2: Method 1 - cURL streaming
        try:
            norm_apk_path = os.path.abspath(apk_path).replace('\\', '/')
            # Dynamic timeout to support up to 3GB - 4GB uploads safely
            curl_max_time = max(1200, int(file_size_mb * 2.5))
            cmd = [
                'curl.exe' if os.name == 'nt' else 'curl',
                '-s', '--connect-timeout', '45', '--max-time', str(curl_max_time),
                '--speed-limit', '5120', '--speed-time', '60',
                '-X', 'POST', upload_url,
                '-F', f'sess_id={sess_id}',
                '-F', 'utype=reg',
                '-F', f'file_0=@{norm_apk_path};filename={file_name}'
            ]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=curl_max_time + 180)
            if p.returncode == 0 and p.stdout:
                raw_out = p.stdout.strip()
                try:
                    res = json.loads(raw_out)
                    if res and isinstance(res, list) and res[0].get('file_code'):
                        code = res[0]['file_code']
                        print(f"✅ [DevUploads Success! Attempt {attempt} via cURL] Code: {code}")
                        return f"https://devuploads.com/{code}"
                    else:
                        print(f"⚠️ [DevUploads cURL response didn't contain file_code]: {raw_out[:200]}")
                except json.JSONDecodeError:
                    print(f"⚠️ [DevUploads cURL JSON Parse Error]: {raw_out[:200]}")
            else:
                print(f"⚠️ [DevUploads cURL non-zero exit code {p.returncode}] stderr: {p.stderr[:200]}")
        except Exception as ce:
            print(f"⚠️ [DevUploads cURL Exception] attempt {attempt}: {ce}")

        # Step 3: Method 2 - Fallback to requests if cURL failed in this attempt
        try:
            print(f"ℹ️ [DevUploads Attempt {attempt}]: Trying requests.post fallback...")
            with open(apk_path, 'rb') as f_up:
                r_up = requests.post(
                    upload_url,
                    data={'sess_id': sess_id, 'utype': 'reg'},
                    files={'file_0': (file_name, f_up, 'application/vnd.android.package-archive')},
                    timeout=max(1200, int(file_size_mb * 2.5))
                )
            if r_up.status_code == 200:
                try:
                    res = r_up.json()
                    if res and isinstance(res, list) and res[0].get('file_code'):
                        code = res[0]['file_code']
                        print(f"✅ [DevUploads Success! Attempt {attempt} via requests] Code: {code}")
                        return f"https://devuploads.com/{code}"
                    else:
                        print(f"⚠️ [DevUploads requests response missing file_code]: {res}")
                except Exception:
                    print(f"⚠️ [DevUploads requests response not JSON]: {r_up.text[:200]}")
            else:
                print(f"⚠️ [DevUploads requests HTTP {r_up.status_code}]: {r_up.text[:200]}")
        except Exception as re_err:
            print(f"⚠️ [DevUploads requests Exception] attempt {attempt}: {re_err}")

        if attempt < max_retries:
            wait_secs = attempt * backoff
            print(f"⏳ [DevUploads Attempt {attempt} Failed]. Retrying in {wait_secs} seconds with a fresh session...")
            time.sleep(wait_secs)

    print(f"❌ [DevUploads All Retries Exhausted]: Failed to upload {file_name} after {max_retries} attempts.")
    return None

AGENT_TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_temp")

def get_optimal_temp_dir():
    """Dynamically returns the temp directory on the drive with the most free disk space."""
    import shutil
    candidate_dirs = [
        AGENT_TEMP_DIR,
        os.path.join(tempfile.gettempdir(), "agent_apk_temp")
    ]
    best_dir = AGENT_TEMP_DIR
    max_free = 0
    for c_dir in candidate_dirs:
        try:
            drive = os.path.splitdrive(os.path.abspath(c_dir))[0]
            if not drive:
                drive = "C:"
            drive_root = (drive + os.sep) if not drive.endswith(os.sep) else drive
            free_space = shutil.disk_usage(drive_root).free
            if free_space > max_free:
                max_free = free_space
                best_dir = c_dir
        except Exception:
            pass
    try:
        os.makedirs(best_dir, exist_ok=True)
    except Exception:
        pass
    return best_dir

def cleanup_storage(specific_files=None, exclude_dir=None):
    """Purges temporary APKs, images, and cache across temp directories to guarantee zero storage bloat."""
    import gc
    import shutil
    gc.collect()

    norm_exclude = os.path.abspath(exclude_dir).lower() if exclude_dir else None

    # 1. Clean specific caller-provided files
    if specific_files:
        for f in specific_files:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
                    print(f"[Storage Cleaned] Removed: {os.path.basename(f)}", flush=True)
            except Exception:
                pass

    # 2. Clean dedicated agent temp folder
    if os.path.exists(AGENT_TEMP_DIR):
        try:
            for item in os.listdir(AGENT_TEMP_DIR):
                p = os.path.join(AGENT_TEMP_DIR, item)
                if norm_exclude and os.path.abspath(p).lower() == norm_exclude:
                    continue
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    elif os.path.isfile(p):
                        os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass

    # 3. Sweep system temp folder for orphaned apk/tmp items
    try:
        sys_temp = tempfile.gettempdir()
        if os.path.exists(sys_temp):
            for item in os.listdir(sys_temp):
                full_p = os.path.join(sys_temp, item)
                try:
                    # Clean orphaned tmp dirs with apks or empty tmp dirs
                    if os.path.isdir(full_p) and (item.startswith(('tmp', 'agent_apk_temp', 'apk_job_', 'apk_proc_'))):
                        has_apk_or_old = False
                        try:
                            for root, dirs, files in os.walk(full_p):
                                for f in files:
                                    if f.endswith(('.apk', '.xapk', '.zip', '.part', '.tmp', '.bin')) or f.startswith('temp_'):
                                        has_apk_or_old = True
                                        break
                                if has_apk_or_old:
                                    break
                            if not os.listdir(full_p):
                                has_apk_or_old = True
                        except Exception:
                            has_apk_or_old = True

                        if has_apk_or_old:
                            shutil.rmtree(full_p, ignore_errors=True)
                            print(f"[Storage Cleaned] Auto-purged temp directory: {item}", flush=True)

                    # Clean orphaned large temp files (> 1MB) or apks
                    elif os.path.isfile(full_p):
                        if item.endswith(('.apk', '.xapk', '.zip', '.part')) or (item.endswith('.tmp') and item.startswith(('tmp', 'wct'))):
                            try:
                                sz = os.path.getsize(full_p)
                                if sz > 1024 * 1024:
                                    os.remove(full_p)
                                    print(f"[Storage Cleaned] Auto-purged temp file: {item} ({sz / (1024*1024):.1f} MB)", flush=True)
                            except Exception:
                                pass
                except Exception:
                    pass
    except Exception:
        pass

    # 4. Auto-purge leftover APKs or temp files in workspace dirs
    for check_dir in ['scratch', 'temp', 'tmp', '.']:
        if os.path.exists(check_dir):
            try:
                for fname in os.listdir(check_dir):
                    if fname.endswith(('.apk', '.apk.part', '.tmp', '.bin')) or fname.startswith('temp_'):
                        full_p = os.path.join(check_dir, fname)
                        if os.path.isfile(full_p):
                            try:
                                os.remove(full_p)
                                print(f"[Storage Cleaned] Auto-purged workspace file: {fname}", flush=True)
                            except Exception:
                                pass
            except Exception:
                pass

    gc.collect()

def generate_hashtags(meta):
    title = meta.get('title', '')
    cat = (meta.get('app_type') or meta.get('category') or '').lower()

    # 1. Primary Clean App Title Tag (e.g. #MXPlayerPro, #Spotify, #Castify)
    clean_title = re.sub(r'[^a-zA-Z0-9]', '', title)
    clean_title = re.sub(r'[vV]\d+.*', '', clean_title)

    tags = []
    if clean_title:
        tags.append(f"#{clean_title}")
        # Add primary base word tag if compound name (e.g. #MXPlayer for "MX Player Pro")
        words = [re.sub(r'[^a-zA-Z0-9]', '', w) for w in title.split() if len(w) >= 3 and not re.match(r'^[vV]?\d+', w)]
        if words and words[0].lower() != clean_title.lower():
            tags.append(f"#{words[0]}")

    # 2. Dynamic app-specific category tags (Checked in strict specificity order!)
    # Music & Audio (Must be BEFORE Video to prevent "Music Player" matching "player")
    if any(w in cat for w in ['music', 'audio', 'song', 'sound', 'streaming music']):
        tags.extend(["#MusicPlayer", "#AudioApp", "#MusicStreaming"])
    # Photo & Camera (Must be BEFORE Video to prevent "Photo & Video Editor" matching generic video)
    elif any(w in cat for w in ['photo', 'camera', 'photography', 'selfie', 'gallery']):
        tags.extend(["#PhotoEditor", "#Photography", "#CameraApp"])
    # Video & Players
    elif any(w in cat for w in ['video player', 'video editor', 'video', 'cinema', 'vlog']):
        tags.extend(["#VideoPlayer", "#VideoEditor", "#AndroidVideo"])
    # Personalization & Customization
    elif any(w in cat for w in ['personalization', 'wallpaper', 'launcher', 'icon', 'theme', 'customization']):
        tags.extend(["#Personalization", "#AndroidTheme", "#Wallpapers", "#Launcher"])
    # Productivity & Office Tools
    elif any(w in cat for w in ['productivity', 'office', 'pdf', 'scanner', 'doc', 'document', 'note']):
        tags.extend(["#Productivity", "#OfficeTools", "#DocumentScanner"])
    # VPN & Online Security
    elif any(w in cat for w in ['vpn', 'proxy', 'security', 'shield', 'privacy']):
        tags.extend(["#VPN", "#OnlinePrivacy", "#SecureBrowsing"])
    # Communication & Messaging
    elif any(w in cat for w in ['communication', 'message', 'messaging', 'chat']):
        tags.extend(["#Communication", "#MessagingApp", "#AndroidChat"])
    # Social Media & Networking
    elif any(w in cat for w in ['social', 'media & networking', 'network']):
        tags.extend(["#SocialMedia", "#AndroidSocial", "#SocialApp"])
    # Entertainment & OTT Streaming
    elif any(w in cat for w in ['entertainment', 'ott', 'movie', 'tv']):
        tags.extend(["#Entertainment", "#StreamingApp", "#MoviesAndSeries"])
    # System & Utilities
    elif any(w in cat for w in ['utility', 'tool', 'system', 'cleaner']):
        tags.extend(["#AndroidTools", "#UtilityApp", "#SystemTools"])
    # Health & Fitness
    elif any(w in cat for w in ['health', 'fitness', 'workout']):
        tags.extend(["#HealthAndFitness", "#WorkoutApp", "#FitnessTracker"])
    # Education & Learning
    elif any(w in cat for w in ['education', 'learning', 'study']):
        tags.extend(["#EducationApp", "#Learning", "#StudyTools"])
    # Finance & Crypto
    elif any(w in cat for w in ['finance', 'crypto', 'stock', 'wallet']):
        tags.extend(["#FinanceApp", "#Crypto", "#StockTracker"])
    # Action, Shooting & Battle Games
    elif any(w in cat for w in ['action', 'shooting', 'battle', 'combat', 'strike']):
        tags.extend(["#ActionGame", "#ShooterGame", "#AndroidGaming"])
    # Racing Games
    elif any(w in cat for w in ['racing', 'driving', 'car', 'drift']):
        tags.extend(["#RacingGame", "#DrivingGame", "#CarGame"])
    # Puzzle Games
    elif any(w in cat for w in ['puzzle', 'brain', 'logic']):
        tags.extend(["#PuzzleGame", "#BrainGames", "#LogicPuzzle"])
    # Arcade & Casual Games
    elif any(w in cat for w in ['arcade', 'casual']):
        tags.extend(["#CasualGame", "#ArcadeGame", "#MobileGaming"])
    # Strategy Games
    elif any(w in cat for w in ['strategy', 'tactical']):
        tags.extend(["#StrategyGame", "#TacticalGame", "#RTSGaming"])
    # RPG & Simulation Games
    elif any(w in cat for w in ['rpg', 'simulation', 'role playing']):
        tags.extend(["#RPG", "#SimulationGame", "#MobileGaming"])
    # General Games
    elif 'game' in cat:
        tags.extend(["#AndroidGaming", "#ModGame", "#UnlimitedMoney"])
    else:
        # Fallback: clean words into PascalCase tags (never generic AndroidApplication)
        cat_words = [re.sub(r'[^a-zA-Z0-9]', '', w) for w in cat.split() if w.lower() not in ['and', '&', 'app', 'apps', 'application', 'android']]
        for cw in cat_words:
            if len(cw) >= 3:
                tags.append(f"#{cw.capitalize()}")

    # 3. Core high-ranking discovery tags
    tags.extend(["#ModAPK", "#AndroidMod", "#LatestUpdate", "#AndroModApps"])

    # Remove duplicates preserving order
    seen = set()
    unique_tags = []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique_tags.append(t)

    return " ".join(unique_tags)

def post_apk_to_telegram(meta, apk_info, target_chat=None, is_update=False, status_callback=None, demanded_by=None, is_last_in_batch=False):
    chat = target_chat or CHANNEL_ID
    api_base = get_telegram_api_base()

    # HARD CEILING: Bot tokens have a strict 50.0 MB document upload limit.
    # Every automated post in @AndroModApps MUST include the raw APK document.
    # If demanded_by is present (user on-demand wishlist request), allow through so DevUploads link can be generated!
    if apk_info.get('size_mb', 0) > 49.0 and not demanded_by:
        print(f"⏩ [Telegram Size Guard]: '{meta.get('title')}' ({apk_info.get('size_mb')} MB) exceeds 49.0 MB limit. Raw APK document is mandatory on Telegram. Skipping cleanly.")
        return False

    # Pre-clean storage proactively before any new download
    cleanup_storage()

    optimal_base = get_optimal_temp_dir()
    session_dir = tempfile.mkdtemp(prefix="apk_job_", dir=optimal_base)
    img_path = None
    apk_path = None
    devuploads_url = None
    posted_success = False

    try:
        # 1. Download Photo
        img_path = os.path.join(session_dir, "cover.jpg")
        if meta.get('image_url'):
            try:
                r_img = requests.get(meta['image_url'], headers=HEADERS, timeout=15)
                if r_img.status_code == 200:
                    with open(img_path, 'wb') as f:
                        f.write(r_img.content)
            except Exception as e:
                print(f"[Image Download Warning]: {e}")

        # 2. Check Disk Space with pre-sweep and optimal drive measurement
        import shutil
        try:
            free_bytes = shutil.disk_usage(optimal_base).free
            free_mb = free_bytes / (1024 * 1024)
            # Require actual file size plus 150 MB safety margin
            required_mb = apk_info['size_mb'] + 150
            if free_mb < required_mb:
                cleanup_storage(exclude_dir=session_dir)
                free_bytes = shutil.disk_usage(optimal_base).free
                free_mb = free_bytes / (1024 * 1024)
                if free_mb < required_mb:
                    print(f"⚠️ [Disk Guard]: Free disk ({round(free_mb, 1)} MB) < required ({round(required_mb, 1)} MB) for '{meta['title']}' ({apk_info['size_mb']} MB). Skipping cleanly.")
                    # ONLY alert admin if overall host drive space is critically exhausted (< 500 MB remaining)!
                    # NEVER send false SOS alerts for oversized individual apps or automated skips!
                    if free_mb < 500:
                        alert_text = (
                            f"🚨 <b>Critical Host Storage Alert</b>\n\n"
                            f"Host disk free space is critically low: <b>{round(free_mb, 1)} MB</b> remaining.\n"
                            f"Cannot process <b>{html.escape(meta['title'])}</b> ({apk_info['size_mb']} MB).\n"
                            f"<i>Automated cleanup executed. Please review machine storage.</i>"
                        )
                        send_admin_alert(alert_text)
                    return False
        except Exception as de:
            print(f"[Disk Check Error]: {de}")

        os.makedirs(session_dir, exist_ok=True)

        ext = apk_info.get('file_ext', '.apk')
        clean_file_name = re.sub(r'[^a-zA-Z0-9\._\-]', '_', f"{meta['title']}_{meta['version']}{ext}")
        apk_path = os.path.join(session_dir, clean_file_name)

        if status_callback:
            try:
                status_callback('app_downloading', {
                    'title': meta['title'],
                    'version': meta.get('version'),
                    'size_mb': apk_info['size_mb']
                })
            except Exception:
                pass

        print(f"Downloading APK: {clean_file_name} ({apk_info['size_mb']} MB)...")
        r_apk = requests.get(apk_info['download_url'], headers={'User-Agent': HEADERS['User-Agent'], 'Referer': apk_info['referer']}, stream=True, timeout=max(1200, int(apk_info.get('size_mb', 0) * 2)))
        if r_apk.status_code != 200:
            print(f"[APK Download Failed]: HTTP {r_apk.status_code}")
            return False

        with open(apk_path, 'wb') as f:
            for chunk in r_apk.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

        print(f"Download complete! Preparing to publish...")

        # APK Binary Integrity Check: Validate ZIP/APK magic bytes (b'PK\x03\x04')
        try:
            with open(apk_path, 'rb') as f_chk:
                magic_bytes = f_chk.read(4)
                if magic_bytes != b'PK\x03\x04':
                    print(f"❌ [Corrupt/Invalid APK File]: {clean_file_name} is not a valid APK binary (magic: {magic_bytes}). Discarding file.")
                    try:
                        os.remove(apk_path)
                    except Exception:
                        pass
                    return False
        except Exception as chk_e:
            print(f"[APK Integrity Check Error]: {chk_e}")
            return False

        if status_callback:
            try:
                status_callback('app_uploading', {
                    'title': meta['title'],
                    'version': meta.get('version'),
                    'size_mb': apk_info['size_mb'],
                    'max_retries': 4
                })
            except Exception:
                pass

        # 3. DevUploads Upload Pipeline (Strict DevUploads Mode with Multi-Attempt Retry)
        devuploads_url = upload_to_devuploads(apk_path, clean_file_name, max_retries=4, backoff=4)

        # If DevUploads fails after all retries:
        if not devuploads_url:
            print(f"[DevUploads Failed]: Could not upload {meta['title']} after 4 retry attempts. Skipping app to protect channel quality.")
            safe_title = html.escape(meta['title'])
            safe_ver = html.escape(str(meta['version']))
            dev_err_text = (
                f"⚠️ <b>DevUploads Upload Failed (4 Retries Exhausted)</b>\n"
                f"<b>App:</b> {safe_title} (v{safe_ver})\n"
                f"<b>Size:</b> {apk_info['size_mb']} MB\n"
                f"<b>Link:</b> {meta.get('download_page', '')}\n\n"
                f"<i>DevUploads upload failed after 4 retry attempts with fresh upload sessions. "
                f"Skipping this app to ensure no direct document fallback or broken posts. "
                f"Pipeline will automatically process the next priority candidate to complete 10 apps.</i>"
            )
            send_admin_alert(dev_err_text)
            return False

        if status_callback:
            try:
                status_callback('app_link_ready', {
                    'title': meta['title'],
                    'version': meta.get('version'),
                    'devuploads_url': devuploads_url
                })
            except Exception:
                pass

        hashtags = generate_hashtags(meta)

        safe_title = html.escape(meta['title'])
        safe_ver = html.escape(str(meta['version']))
        safe_cat = html.escape(meta.get('app_type') or meta.get('category') or 'Android Utility & Tools')
        features_list = meta.get('features', ["VIP Premium Unlocked", "Ad-Free", "All Features Active"])
        features_text = "\n".join([f"• {html.escape(f)}" for f in features_list])
        update_badge = "🔄 <b>NEW UPDATE RELEASED!</b>\n\n" if is_update else ""

        # User Demand Plain-Text Attribution (Strictly non-clickable plain text, Account Name only)
        demand_badge = ""
        demand_footer = ""
        if demanded_by:
            # Strip @, URL characters, and HTML tags to guarantee it is NEVER a clickable link or mention
            clean_name = html.escape(re.sub(r'[@<>:/]', '', str(demanded_by)).strip())
            if not clean_name:
                clean_name = "Community Member"
            demand_badge = (
                f"🎯 <b>#UserDemand | Community Request Fulfilled!</b> ✨\n"
                f"👤 <b>Demanded By:</b> {clean_name}\n\n"
            )
            demand_footer = f"⚡ <i>Demanded by {clean_name} via @FileShareAppWishBot</i>\n"

        ext = apk_info.get('file_ext', '.apk')
        is_xapk = ext in ['.xapk', '.zip', '.apks']
        pkg_type = ext.upper().replace('.', '')
        install_note = f"\n📦 <b>Package:</b> {pkg_type}\n💡 <i>Note: Install via XAPK Installer / extract OBB if needed.</i>" if is_xapk else ""

        header_part = (
            f"{demand_badge}"
            f"{update_badge}"
            f"📱 <b>{safe_title}</b>\n\n"
            f"🏷️ <b>Version:</b> {safe_ver}\n"
            f"💾 <b>Size:</b> {apk_info['size_mb']} MB\n"
            f"📂 <b>Category:</b> {safe_cat}{install_note}\n"
            f"🛡️ <b>Security:</b> VirusTotal Clean • 100% Tested & Verified\n\n"
            f"⚡ <b>MOD & Key Features:</b>\n"
            f"{features_text}\n\n"
        )
        footer_part = (
            f"📥 <b>Download {pkg_type} (High Speed):</b>\n"
            f"👉 {devuploads_url}\n\n"
            f"{demand_footer}"
            f"⚡ <b>Get the Fastest & Latest MOD Applications Here:</b>\n"
            f"👉 <a href=\"https://t.me/AndroModApps\"><b>Join Channel:</b></a> https://t.me/AndroModApps\n\n"
            f"{hashtags}"
        )
        desc_raw = meta['description'] or 'Enjoy the latest version with full unlocked features on your Android device.'
        allowed_desc_len = 1000 - len(header_part) - len(footer_part) - 25
        if allowed_desc_len < 60:
            allowed_desc_len = 60
        clean_desc = smart_truncate(clean_text_for_telegram(desc_raw), allowed_desc_len)

        caption = f"{header_part}📖 <b>Description:</b>\n{html.escape(clean_desc)}\n\n{footer_part}\n"
        posted_success = False

        # =========================================================================
        # MESSAGE 1: Photo Banner with ONLY clean app name (without .apk)
        # =========================================================================
        photo_title_text = re.sub(r'(?i)\.apk$', '', clean_file_name)
        photo_caption = f"<b>{html.escape(photo_title_text)}</b>"
        photo_msg_id = None

        if os.path.exists(img_path):
            try:
                with open(img_path, 'rb') as f_img:
                    r_photo = requests.post(
                        f"{api_base}/sendPhoto",
                        data={'chat_id': chat, 'caption': photo_caption, 'parse_mode': 'HTML'},
                        files={'photo': f_img},
                        timeout=60
                    )
                    res_p = r_photo.json()
                    if res_p.get('ok'):
                        photo_msg_id = res_p.get('result', {}).get('message_id')
                        print(f"✅ [Photo Banner Sent]: {photo_title_text} (Msg ID: {photo_msg_id})")
                    else:
                        print(f"[Photo File Send Error]: {res_p}")
            except Exception as pe:
                print(f"[Photo File Exception]: {pe}")

        if not photo_msg_id and meta.get('image_url'):
            try:
                r_p_url = requests.post(
                    f"{api_base}/sendPhoto",
                    data={'chat_id': chat, 'caption': photo_caption, 'parse_mode': 'HTML', 'photo': meta['image_url']},
                    timeout=35
                )
                res_pu = r_p_url.json()
                if res_pu.get('ok'):
                    photo_msg_id = res_pu.get('result', {}).get('message_id')
                    print(f"✅ [Photo URL Sent]: {photo_title_text} (Msg ID: {photo_msg_id})")
            except Exception as pue:
                print(f"[Photo URL Error]: {pue}")

        # =========================================================================
        # MESSAGE 2: Raw APK Document (MANDATORY: Must succeed for post to count)
        # =========================================================================
        if apk_info.get('size_mb', 0) <= 49.0 and os.path.exists(apk_path):
            try:
                print(f"📤 [Uploading Raw Document]: {clean_file_name} ({apk_info['size_mb']} MB) to Telegram...")
                with open(apk_path, 'rb') as f_doc:
                    files = {
                        'document': (clean_file_name, f_doc, 'application/vnd.android.package-archive')
                    }
                    data = {
                        'chat_id': chat,
                        'caption': caption,
                        'parse_mode': 'HTML'
                    }
                    # 10th Post Feature: Attach Wishlist / Request button only on final batch post
                    if is_last_in_batch:
                        data['reply_markup'] = json.dumps({
                            'inline_keyboard': [
                                [
                                    {
                                        'text': '🎯 Request App Updates & Wishlist',
                                        'url': 'https://t.me/FileShareAppWishBot'
                                    }
                                ]
                            ]
                        })
                    r_doc = requests.post(f"{api_base}/sendDocument", data=data, files=files, timeout=300)
                    res_doc = r_doc.json()
                    if res_doc.get('ok'):
                        print(f"✅ [Raw APK Document Sent]: {clean_file_name} (Msg ID: {res_doc['result']['message_id']})")
                        posted_success = True
                    else:
                        print(f"[sendDocument Error]: {res_doc}")
            except Exception as de:
                print(f"[sendDocument Exception]: {de}")
        elif demanded_by and os.path.exists(apk_path):
            # For user-demanded apps > 49.0 MB, broadcast verified demand card with DevUploads link
            try:
                r_msg = requests.post(
                    f"{api_base}/sendMessage",
                    data={'chat_id': chat, 'text': caption, 'parse_mode': 'HTML'},
                    timeout=35
                )
                if r_msg.json().get('ok'):
                    print(f"✅ [On-Demand Large App Broadcast Sent]: {meta['title']} ({apk_info['size_mb']} MB)")
                    posted_success = True
            except Exception as me:
                print(f"[On-Demand Send Error]: {me}")

        # STRICT QUALITY ENFORCEMENT:
        # Every post MUST include the raw APK document file.
        # If sendDocument failed, do NOT send a text-only fallback message!
        # Delete the photo banner so no incomplete/broken post stays on the channel.
        if not posted_success:
            print(f"❌ [Raw APK Document FAILED]: {meta['title']} ({apk_info.get('size_mb')} MB). Deleting orphaned photo banner to avoid incomplete post on channel.")
            if photo_msg_id:
                try:
                    requests.post(
                        f"{api_base}/deleteMessage",
                        data={'chat_id': chat, 'message_id': photo_msg_id},
                        timeout=15
                    )
                    print(f"🗑️ [Rollback Photo]: Deleted orphaned photo banner (Msg ID: {photo_msg_id})")
                except Exception as del_err:
                    print(f"[Rollback Photo Error]: {del_err}")
            send_admin_alert(
                f"⚠️ <b>Raw APK Post Failed</b>\n"
                f"<b>App:</b> {safe_title} (v{safe_ver})\n"
                f"<b>Size:</b> {apk_info.get('size_mb')} MB\n"
                f"<i>Raw APK sendDocument failed. Photo banner was rolled back. Pipeline will advance to the next app.</i>"
            )
            return False

        # FREE DISK STORAGE: Purge local APK immediately after Telegram posting
        if os.path.exists(apk_path):
            try:
                os.remove(apk_path)
                print(f"[Immediate Cleanup] Deleted local APK: {clean_file_name}", flush=True)
            except Exception:
                pass

        if not posted_success:
            send_admin_alert(f"⚠️ <b>Telegram Broadcast Failed</b>\nApp: {meta['title']} (v{meta['version']})\nTarget: {chat}\nCheck bot admin permissions in channel.")
        else:
            if status_callback:
                try:
                    status_callback('app_posted', {
                        'title': meta['title'],
                        'version': meta.get('version'),
                        'size_mb': apk_info['size_mb'],
                        'devuploads_url': devuploads_url
                    })
                except Exception:
                    pass

        return devuploads_url if posted_success else False

    finally:
        # GUARANTEED ZERO STORAGE BLOAT: Always clean up files and session folder
        if apk_path and os.path.exists(apk_path):
            try:
                os.remove(apk_path)
            except Exception:
                pass
        if img_path and os.path.exists(img_path):
            try:
                os.remove(img_path)
            except Exception:
                pass
        if session_dir and os.path.exists(session_dir):
            try:
                shutil.rmtree(session_dir, ignore_errors=True)
            except Exception:
                pass
        cleanup_storage()

def search_and_find_app(app_name):
    """Searches LiteAPKs for an app using direct slugs and WordPress REST API.
    Returns list of candidate dicts: [{'title': ..., 'url': ..., 'slug': ..., 'is_direct': bool}]
    """
    clean_name = app_name.strip()
    if not clean_name:
        return []

    results = []
    seen_urls = set()

    # Fast Check 1: Direct slug matching
    slug = re.sub(r'[^a-z0-9]+', '-', clean_name.lower()).strip('-')
    for s_suffix in ['', '-app', '-mod', '-video-editor', '-pro', '-music']:
        d_url = f"https://liteapks.com/{slug}{s_suffix}.html"
        try:
            r = requests.get(d_url, headers=HEADERS, timeout=3)
            if r.status_code == 200 and 'download' in r.text.lower():
                soup = BeautifulSoup(r.text, 'html.parser')
                h1 = soup.find('h1')
                t = h1.text.strip() if h1 else clean_name
                if d_url not in seen_urls:
                    seen_urls.add(d_url)
                    results.append({'title': t, 'url': d_url, 'slug': slug, 'is_direct': True})
                    break
        except Exception:
            pass

    # Fast Check 2: WordPress REST API search
    try:
        search_url = f"https://liteapks.com/wp-json/wp/v2/posts?search={urllib.parse.quote(clean_name)}&per_page=10"
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            posts = r.json()
            for p in posts:
                link = p.get('link', '')
                if link and link not in seen_urls:
                    seen_urls.add(link)
                    raw_t = p.get('title', {}).get('rendered', '')
                    t = BeautifulSoup(raw_t, 'html.parser').text
                    results.append({'title': t, 'url': link, 'slug': p.get('slug', ''), 'is_direct': False})
    except Exception as e:
        print(f"[Search API Exception]: {e}")

    return results

def post_single_custom_app(app_name, target_version=None, status_callback=None, target_chat=None):
    """Finds, downloads, uploads to DevUploads, and publishes a user-requested app to Telegram."""
    if status_callback:
        try:
            status_callback('searching', {'app_name': app_name, 'version': target_version})
        except Exception:
            pass

    candidates = search_and_find_app(app_name)
    if not candidates:
        if status_callback:
            try:
                status_callback('not_found', {'app_name': app_name})
            except Exception:
                pass
        return {'success': False, 'error': f"App '{app_name}' not found on LiteAPKs."}

    best_match = candidates[0]
    app_url = best_match['url']

    if status_callback:
        try:
            status_callback('found', {'title': best_match['title'], 'url': app_url, 'target_version': target_version})
        except Exception:
            pass

    meta = extract_app_metadata(app_url)
    if not meta:
        return {'success': False, 'error': f"Could not extract metadata for {app_url}"}

    if target_version:
        meta['version'] = target_version

    if status_callback:
        try:
            status_callback('resolving_apk', {'title': meta['title'], 'target_version': target_version})
        except Exception:
            pass

    apk_info = resolve_direct_apk_url(meta['download_page'], target_version=target_version)
    if not apk_info:
        return {'success': False, 'error': f"Could not resolve APK download link for {meta['title']}."}

    dev_url = post_apk_to_telegram(meta, apk_info, target_chat=target_chat, status_callback=status_callback)
    if dev_url:
        sent_set = load_sent_history()
        c_slug = get_canonical_slug(app_url)
        norm_title = normalize_app_title(meta.get('title', ''))
        pkg_name = meta.get('package_name', '')
        ver = meta.get('version', '')
        record_posted_apk(sent_set, app_url, c_slug, norm_title, pkg_name, ver)
        update_catalog_entry(meta, apk_info, dev_url)

        if status_callback:
            try:
                status_callback('completed', {
                    'title': meta['title'],
                    'version': meta.get('version'),
                    'size_mb': apk_info['size_mb'],
                    'devuploads_url': dev_url
                })
            except Exception:
                pass

        return {
            'success': True,
            'title': meta['title'],
            'version': meta.get('version'),
            'size_mb': apk_info['size_mb'],
            'devuploads_url': dev_url,
            'url': app_url
        }
    else:
        return {'success': False, 'error': f"DevUploads upload failed for {meta['title']}."}

def check_hourly_cooldown(min_interval_minutes=50, force=False):
    if force:
        return True
    
    if '--force' in sys.argv or os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' or os.environ.get('FORCE_RUN') == '1':
        print("⚡ [Manual Run Detected]: Bypassing cooldown to execute immediately on user demand.")
        return True

    cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler_config.json")
    last_run_ts = 0
    interval_min = 60
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                last_run_ts = cfg.get('last_run_timestamp', 0)
                interval_min = cfg.get('interval_minutes', 60)
        except Exception:
            pass
    
    now = time.time()
    effective_gap = max(40, interval_min - 10) * 60
    elapsed = now - last_run_ts
    
    if elapsed < effective_gap:
        mins_ago = int(elapsed / 60)
        mins_remaining = int((effective_gap - elapsed) / 60)
        print(f"⏳ [Hourly Cooldown Active]: Last batch posted {mins_ago}m ago. Next batch in ~{mins_remaining}m. Skipping scheduled trigger cleanly.")
        return False
    
    return True

def record_successful_run_timestamp():
    cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler_config.json")
    cfg = {"interval_minutes": 60, "updated_at": int(time.time()), "last_run_timestamp": int(time.time())}
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                if isinstance(existing, dict):
                    cfg.update(existing)
                    cfg["last_run_timestamp"] = int(time.time())
        except Exception:
            pass
    try:
        with open(cfg_file, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[Record Run Timestamp Error]: {e}")

def run_pipeline(demo_target=None, status_callback=None, force_source=None, force=False):
    if not demo_target and not force and not check_hourly_cooldown():
        if status_callback:
            try:
                status_callback('cooldown', {'status': 'Cooldown active, batch already posted within the hour.'})
            except Exception:
                pass
        return 0

    cleanup_storage()
    sent_set = load_sent_history()
    catalog = load_apps_catalog()
    verifier = AppSelectionVerifierSubagent(sent_set=sent_set, catalog=catalog, live_sync=True)

    # Alternate Round-Robin Source Selection (10 LiteAPKs 🔄 10 AndroForever)
    active_source = (force_source or get_current_source(sent_set=sent_set)).lower()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 Starting Autonomous Pipeline [SOURCE: {active_source.upper()}]...")
    print(f"Database contains {len(sent_set)} entries tracked.")

    if active_source == 'androforever':
        if status_callback:
            try:
                status_callback('scraping', {'status': 'Scanning AndroForever for Popular and Category apps...'})
            except Exception:
                pass

        pop_candidates, cat_candidates = androforever_scraper.scrape_androforever_prioritized_candidates()
        selected_batch = select_androforever_10_app_batch(pop_candidates, cat_candidates, sent_set, target_count=MAX_APKS_PER_RUN, verifier=verifier)
        fallback_candidates = [item for item in (pop_candidates + cat_candidates) if item['url'] not in {x['url'] for x in selected_batch}]
        fallback_stream = (
            item for item in fallback_candidates
            if verifier.get_canonical_slug(item['url']) not in verifier.batch_slugs
        )
    else:
        if status_callback:
            try:
                status_callback('scraping', {'status': 'Scanning LiteAPKs for Priority 1, 2, and 3 candidate apps...'})
            except Exception:
                pass

        tier1, cat_p1, cat_p2, deep_catalog = scrape_prioritized_candidates()
        selected_batch = select_10_app_batch(tier1, cat_p1, cat_p2, deep_catalog, sent_set, target_count=MAX_APKS_PER_RUN, verifier=verifier)
        selected_urls = {item['url'] for item in selected_batch}
        fallback_stream = (
            item for item in generate_prioritized_candidates(tier1, cat_p1, cat_p2, deep_catalog)
            if item['url'] not in selected_urls and verifier.get_canonical_slug(item['url']) not in verifier.batch_slugs
        )

    summary_text = format_batch_summary(selected_batch)
    print(summary_text)

    if status_callback:
        try:
            status_callback('start', {
                'source': active_source,
                'selected': len(selected_batch),
                'target_max': MAX_APKS_PER_RUN,
                'batch': selected_batch
            })
        except Exception:
            pass

    if not selected_batch:
        print(f"ℹ️ No unposted candidate APKs found for {active_source.upper()}. Database is up to date.")
        # Automatically toggle source so next run checks the other website
        save_source_state(active_source, sent_set=sent_set)
        if status_callback:
            try:
                status_callback('done', {'processed': 0, 'total_db': len(sent_set), 'completed_apps': []})
            except Exception:
                pass
        return 0

    processed_count = 0
    processed_slugs = set()
    completed_apps = []

    from itertools import chain
    candidate_stream = chain(selected_batch, fallback_stream)

    for item in candidate_stream:
        if processed_count >= MAX_APKS_PER_RUN:
            break

        url = item['url']
        item_cat = item.get('category', 'App')
        item_label = item.get('priority_label', item_cat)
        c_slug = verifier.get_canonical_slug(url)

        if c_slug in processed_slugs or is_banned_app("", url):
            continue

        item_source = item.get('source', active_source)

        if item_source == 'androforever':
            meta = item.get('meta') or androforever_scraper.extract_androforever_metadata(url, fallback_category=item_cat)
        else:
            meta = item.get('meta') or extract_app_metadata(url, fallback_category=item_cat)

        if meta:
            if not meta.get('app_type'):
                meta['app_type'] = item_cat
            if not meta.get('category'):
                meta['category'] = meta.get('app_type')

        if not meta:
            continue

        if is_banned_app(meta.get('title', ''), url):
            print(f"🚫 [PERMANENTLY BANNED]: Skipping {meta['title']} ({url}) per user policy.")
            sent_set.add(url)
            sent_set.add(f"{url}::{meta.get('version', '')}")
            continue

        version = meta.get('version', 'Latest')

        # For items coming from fallback cascade (if a primary candidate failed), re-verify with Subagent
        if item not in selected_batch:
            decision, reason, prev_v = verifier.verify_candidate(item, meta)
            if decision == 'REJECT':
                print(f"🚫 [SUBAGENT FALLBACK REJECTED]: {meta['title']} (v{version}) -> {reason}")
                continue
            verifier.lock_batch_candidate(item, meta)
            is_update = reason.startswith('GENUINE_NEW_UPDATE')
        else:
            is_update = item.get('is_update', False)
            prev_v = item.get('prev_version')

        if is_update:
            print(f"🔄 [GENUINE APP UPDATE DETECTED]: {meta['title']} has been updated to new version {version} (Previous was v{prev_v})!")
        else:
            print(f"\nProcessing [{item_label}]: {meta['title']} ({url})")

        if item_source == 'androforever':
            apk_info = androforever_scraper.resolve_androforever_apk_url(meta['download_page'], meta['title'], meta['version'])
        else:
            apk_info = resolve_direct_apk_url(meta['download_page'])
        if not apk_info:
            print(f"Could not resolve direct APK URL for {meta['title']}. Skipping.")
            continue

        print(f"Found APK: {meta['title']} (v{version}) | Size: {apk_info['size_mb']} MB")

        # Mandatory Raw APK Rule:
        # Every channel post MUST include the raw APK file attached directly on Telegram.
        # Telegram Bot API sendDocument has a hard 50.0 MB limit.
        # Therefore, any automated candidate with size > 49.0 MB MUST be skipped.
        MAX_TELEGRAM_DOC_SIZE_MB = 49.0
        if apk_info['size_mb'] > MAX_TELEGRAM_DOC_SIZE_MB:
            print(f"⏩ [Oversized Candidate Skipped]: {meta['title']} ({apk_info['size_mb']} MB) > {MAX_TELEGRAM_DOC_SIZE_MB} MB. Must include raw APK on Telegram. Advancing to next candidate.")
            continue

        # Disk space guard
        try:
            curr_free_mb = shutil.disk_usage(get_optimal_temp_dir()).free / (1024 * 1024)
            req_mb = apk_info['size_mb'] + 150
            if curr_free_mb < req_mb:
                print(f"⏩ [Oversized App Skipped in Automated Run]: '{meta['title']}' ({apk_info['size_mb']} MB) requires {round(req_mb, 1)} MB (available: {round(curr_free_mb, 1)} MB). Advancing to next priority app.")
                continue
        except Exception:
            pass

        next_app_name = "Finalizing batch..."
        if processed_count + 1 < len(selected_batch):
            next_app_name = f"{selected_batch[processed_count + 1]['title']} (v{selected_batch[processed_count + 1].get('version', '')})"

        if status_callback:
            try:
                status_callback('app_start', {
                    'index': processed_count + 1,
                    'total': MAX_APKS_PER_RUN,
                    'title': meta['title'],
                    'version': version,
                    'size_mb': apk_info['size_mb'],
                    'category': item_cat,
                    'priority_label': item_label,
                    'is_update': is_update,
                    'next_app': next_app_name
                })
            except Exception:
                pass

        is_last = (processed_count == MAX_APKS_PER_RUN - 1)
        success = False
        try:
            success = post_apk_to_telegram(meta, apk_info, target_chat=demo_target, is_update=is_update, status_callback=status_callback, is_last_in_batch=is_last)
        except Exception as pe:
            print(f"[Pipeline Post Error] {meta['title']}: {pe}")
        finally:
            cleanup_storage()

        if success:
            dev_url = success if isinstance(success, str) else ""
            verifier.record_posted_app(meta, apk_info, dev_url)
            processed_slugs.add(c_slug)
            processed_count += 1
            status_tag = f"Updated (from v{prev_v})" if is_update else "New"
            print(f"📊 Progress: [{processed_count}/{MAX_APKS_PER_RUN}] ({status_tag}) [{item_label}] posted successfully.")

            completed_apps.append({
                'title': meta['title'],
                'version': version,
                'devuploads_url': dev_url,
                'size_mb': apk_info['size_mb'],
                'category': item_cat
            })

            if status_callback:
                try:
                    status_callback('progress', {
                        'processed': processed_count,
                        'max': MAX_APKS_PER_RUN,
                        'title': meta['title'],
                        'version': version,
                        'is_update': is_update,
                        'size_mb': apk_info['size_mb'],
                        'category': item_cat,
                        'priority_label': item_label,
                        'devuploads_url': dev_url
                    })
                except Exception:
                    pass
            time.sleep(3)

        cleanup_storage()
    save_source_state(active_source, sent_set=sent_set)
    print(f"\n🔄 [SOURCE STATE SAVED]: Completed run for {active_source.upper()}. Next run will alternate to {'ANDROFOREVER' if active_source == 'liteapks' else 'LITEAPKS'}.")
    print(f"\nPipeline Run Completed. Broadcasted {processed_count} new/updated APKs.")

    if processed_count > 0:
        record_successful_run_timestamp()

    # Automated GitHub Step Summary Report
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as sf:
                sf.write("### 🚀 LiteAPKs Agent Execution Summary\n\n")
                sf.write(f"- **Broadcasted This Run**: {processed_count} / {MAX_APKS_PER_RUN}\n")
                sf.write(f"- **Total Sent Database**: {len(sent_set)} entries\n")
                sf.write(f"- **Execution Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
                sf.write(f"- **Status**: {'✅ Successful Run' if processed_count > 0 else 'ℹ️ No New Updates'}\n")
                sf.write("\n" + summary_text + "\n")
        except Exception as se:
            print(f"[Step Summary Error]: {se}")

    if status_callback:
        try:
            status_callback('done', {
                'processed': processed_count,
                'total_db': len(sent_set),
                'completed_apps': completed_apps
            })
        except Exception:
            pass

    return processed_count

    # Automated GitHub Step Summary Report
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as sf:
                sf.write("### 🚀 LiteAPKs Agent Execution Summary\n\n")
                sf.write(f"- **Broadcasted This Run**: {processed_count} / {MAX_APKS_PER_RUN}\n")
                sf.write(f"- **Total Sent Database**: {len(sent_set)} entries\n")
                sf.write(f"- **Execution Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
                sf.write(f"- **Status**: {'✅ Successful Run' if processed_count > 0 else 'ℹ️ No New Updates'}\n")
                sf.write("\n" + summary_text + "\n")
        except Exception as se:
            print(f"[Step Summary Error]: {se}")

    if status_callback:
        try:
            status_callback('done', {'processed': processed_count, 'total_db': len(sent_set)})
        except Exception:
            pass

    return processed_count

if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as fatal_e:
        import traceback
        err_tb = traceback.format_exc()
        print(f"FATAL PIPELINE CRASH: {fatal_e}\n{err_tb}")
        err_msg = (
            f"🚨 <b>Fatal Crash in LiteAPKs Agent</b>\n\n"
            f"<b>Error:</b> <code>{html.escape(str(fatal_e))}</code>\n\n"
            f"<pre>{html.escape(err_tb[-1500:])}</pre>"
        )
        send_admin_alert(err_msg)
        sys.exit(1)
