import os
import sys
import re
import json
import html
import time
import base64
import urllib.parse
import tempfile
import subprocess
import requests
from concurrent.futures import ThreadPoolExecutor

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    ADMIN_CHAT_ID,
    DEVUPLOADS_API_KEY,
    SOURCE_BASE_URL,
    MAX_APKS_PER_RUN,
    DATABASE_FILE
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
            print(f"[Admin Alert Failed]: {res.status_code} - {res.text}")
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
        items = list(sent_set)
        if len(items) > 4000:
            items = items[-4000:]
        with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2)
    except Exception as e:
        print(f"[Database Save Error]: {e}")

def get_telegram_api_base():
    if LOCAL_BOT_API_URL:
        return f"{LOCAL_BOT_API_URL.rstrip('/')}/bot{BOT_TOKEN}"
    return f"https://api.telegram.org/bot{BOT_TOKEN}"

def clean_text_for_telegram(text):
    if not text:
        return ""
    # Strip HTML tags except standard b, i, code
    text = re.sub(r'</?(?:div|p|span|a|ul|li|h\d)[^>]*>', ' ', text)
    # Strip any URLs to enforce 100% NO external links policy
    text = re.sub(r'https?://[^\s<>"\']+', '', text)
    # Strip website names/mentions
    text = re.sub(r'(?i)liteapks(?:\.com)?', '', text)
    # Enforce STRICT ENGLISH ONLY: Strip Hindi / Devanagari characters
    text = re.sub(r'[\u0900-\u097F]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

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

# Mapping category slugs to rich, descriptive App Types ("किस टाइप का ऐप है")
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

def scrape_latest_apk_links():
    """Scrapes LiteAPKs with strict Priority per user requirements:
    1. Tier 1 (First Priority):
       - Main 'Top Apps' from /apps (e.g. CapCut, Alight Motion, YouTube ReVanced, Spotify, Truecaller)
       - Main 'Top Games' from /games (e.g. Roblox, 8 Ball Pool, Shadow Fight 2, Subway Surfers)
       - Homepage 'Indispensable on your phone' & 'Must have collection'
    2. Tier 2 (Second Priority):
       - Category-wise Top Apps & Games: Scrapes ALL 50 subcategories under /apps and /games:
         (Art & Design, Auto & Vehicles, Beauty, Books & Reference, Business, Comics, Communication,
          Education, Entertainment, Finance, Food & Drink, Health & Fitness, House & Home, Lifestyle,
          Maps & Navigation, Music & Audio, News & Magazines, Personalization, Photography, Productivity,
          Social, Tools, Travel & Local, Video Players & Editors, Weather, and all Game genres).
         Fetches top 3 highest ranked apps/games from EACH category in parallel.
    3. Tier 3 (Third Priority - All Remaining Apps & Games CATEGORY-WISE):
       - All remaining apps and games from every category (apps after top 3 in all categories, across Page 1 & Page 2).
       - Arranged strictly in the EXACT SAME Category Priority Order as Tier 2 (Video Players, Music & Audio, Action Games, Photography, Tools, Entertainment, etc. come FIRST!).
       - Deep general pagination fallback (Pages 3 to 5) ensures 100% comprehensive coverage.
    """
    tier1_top_apps = []
    tier2_category_top_apps = []
    tier3_remaining_apps_and_games = []

    # 1. Tier 1: Main "Top Apps" and "Top Games" from /apps, /games and Homepage
    # Completely DYNAMIC & Auto-Scaling (Zero Hardcoded Limits):
    # Automatically extracts ALL apps in top carousels (scroll-section, #featured-track, top-app-item)
    # adapting seamlessly if LiteAPKs has 10, 15, 20, or 50 apps now or in the future!
    for root_url in [f"{SOURCE_BASE_URL}/", f"{SOURCE_BASE_URL}/apps", f"{SOURCE_BASE_URL}/games"]:
        try:
            r = requests.get(root_url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                html = r.text
                page_top_apps = []

                # Strategy A: All top-app-item elements (dynamic, scales to any future count)
                top_items = re.findall(r'<a[^>]+class=[\"\'][^\"\']*top-app-item[^\"\']*[\"\'][^>]+href=[\"\'](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)[\"\']', html)
                if not top_items:
                    top_items = re.findall(r'<a[^>]+href=[\"\'](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)[\"\'][^>]+class=[\"\'][^\"\']*top-app-item[^\"\']*[\"\']', html)
                for u in top_items:
                    if not is_banned_app("", u) and u not in page_top_apps and u not in tier1_top_apps:
                        page_top_apps.append(u)

                # Strategy B: All scroll-section carousels (e.g. "Indispensable on your phone", "Top Apps", "Top Games", #featured-track)
                scroll_matches = re.findall(r'<div[^>]*class=[\"\'][^\"\']*(?:scroll-section|overflow-x-auto)[^\"\']*[\"\'][^>]*>([\s\S]*?)</div>\s*</div>', html)
                featured_m = re.findall(r'<div[^>]*id=[\"\']featured-track[\"\'][^>]*>([\s\S]*?)</div>\s*</div>', html)
                for sec_html in scroll_matches + featured_m:
                    for u in re.findall(r'href=[\"\'](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)[\"\']', sec_html):
                        if not is_banned_app("", u) and u not in page_top_apps and u not in tier1_top_apps:
                            page_top_apps.append(u)

                # Add all dynamically detected top apps to Tier 1
                tier1_top_apps.extend(page_top_apps)

                # All remaining general apps on the page go to Tier 3 (remaining pool)
                all_page_links = re.findall(r'href=[\"\'](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)[\"\']', html)
                for u in all_page_links:
                    if not is_banned_app("", u) and u not in tier1_top_apps and u not in tier3_remaining_apps_and_games:
                        tier3_remaining_apps_and_games.append(u)
        except Exception as e:
            print(f"[Tier 1 Scraper Error] {root_url}: {e}")

    # 2. Tier 2 & 3: Built-in + Dynamically Discovered Categories across /apps and /games
    all_categories = list(ALL_BUILTIN_CATEGORIES)
    seen_cat_urls = {u for _, u in all_categories}
    for hub_url in [f"{SOURCE_BASE_URL}/apps", f"{SOURCE_BASE_URL}/games"]:
        try:
            r_hub = requests.get(hub_url, headers=HEADERS, timeout=8)
            if r_hub.status_code == 200:
                html = r_hub.text
                cat_links = re.findall(r'href=[\"\'](https://liteapks\.com/(?:apps|games)/[a-zA-Z0-9\-_]+)[\"\']', html)
                for l in cat_links:
                    if l not in seen_cat_urls and not l.endswith('/apps') and not l.endswith('/games') and '/page/' not in l and not is_banned_app("", l):
                        seen_cat_urls.add(l)
                        clean_name = l.rstrip('/').split('/')[-1].replace('-', ' ').title()
                        all_categories.append((clean_name, l))
        except Exception:
            pass

    def fetch_category_apps(cat_item):
        cat_name, cat_url = cat_item
        apps = []
        # Multi-page category depth: Scrapes Page 1 and Page 2 of every category
        # ensuring all apps are strictly grouped and ranked under their exact category
        for page_target in [cat_url, f"{cat_url}/page/2"]:
            try:
                r_cat = requests.get(page_target, headers=HEADERS, timeout=8)
                if r_cat.status_code == 200:
                    html = r_cat.text

                    # Strategy 1 (Universal Top-App Identifier): Extract all top-app-item elements
                    top_items = re.findall(r"""<a[^>]+class=['"][^'"]*top-app-item[^'"]*['"][^>]+href=['"](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)['"]""", html)
                    if not top_items:
                        top_items = re.findall(r"""<a[^>]+href=['"](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)['"][^>]+class=['"][^'"]*top-app-item[^'"]*['"]""", html)
                    for a in top_items:
                        if a not in apps and not is_banned_app("", a):
                            apps.append(a)

                    # Strategy 2 (Carousel Container Fallback): Any remaining apps in scroll-section container
                    scroll_m = re.search(r"""<div[^>]*class=['"][^'"]*scroll-section[^'"]*['"][^>]*>([\s\S]*?)</div>\s*</div>""", html)
                    if scroll_m:
                        sec_links = re.findall(r"""href=['"](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)['"]""", scroll_m.group(1))
                        for a in sec_links:
                            if a not in apps and not is_banned_app("", a):
                                apps.append(a)

                    # Strategy 3 (Dynamic Full Category Grid): Automatically captures all other apps on category page
                    cat_apps = re.findall(r"""href=['"](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)['"]""", html)
                    for a in cat_apps:
                        if a not in apps and not is_banned_app("", a):
                            apps.append(a)
            except Exception:
                pass
        return cat_name, apps

    if all_categories:
        with ThreadPoolExecutor(max_workers=16) as executor:
            cat_results = list(executor.map(fetch_category_apps, all_categories))
        
        seen_all = set(tier1_top_apps)

        # 2. Tier 2 (Second Priority): Top 3 ranked apps from EACH category in category priority order
        # (Rank 1 of all categories, then Rank 2, then Rank 3)
        for rank in range(3):
            for cat_name, apps in cat_results:
                if rank < len(apps):
                    a = apps[rank]
                    if a not in seen_all:
                        seen_all.add(a)
                        tier2_category_top_apps.append(a)

        # 3. Tier 3 (Third Priority): ALL remaining apps also ordered CATEGORY-WISE in the EXACT SAME category priority order!
        # (Rank 4 of all categories, then Rank 5, then Rank 6... all the way to deepest rank)
        # Guarantees remaining apps from high-demand categories (Video, Music, Action, Photo, Tools) come FIRST!
        max_depth = max(len(apps) for _, apps in cat_results) if cat_results else 0
        for rank in range(3, max_depth):
            for cat_name, apps in cat_results:
                if rank < len(apps):
                    a = apps[rank]
                    if a not in seen_all:
                        seen_all.add(a)
                        tier3_remaining_apps_and_games.append(a)

    # 4. Fallback Catalog Pool: Deep general pagination for /apps and /games (Pages 3 to 5)
    def fetch_pagination_page(p_url):
        try:
            r_p = requests.get(p_url, headers=HEADERS, timeout=8)
            if r_p.status_code == 200:
                return re.findall(r"""href=['"](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)['"]""", r_p.text)
        except Exception:
            pass
        return []

    pagination_urls = []
    for p in range(3, 6):
        pagination_urls.append(f"{SOURCE_BASE_URL}/apps/page/{p}")
        pagination_urls.append(f"{SOURCE_BASE_URL}/games/page/{p}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        page_results = list(executor.map(fetch_pagination_page, pagination_urls))

    for p_apps in page_results:
        for a in p_apps:
            if not is_banned_app("", a) and a not in seen_all:
                seen_all.add(a)
                tier3_remaining_apps_and_games.append(a)

    # Merge in strict Priority Order
    final_prioritized = tier1_top_apps + tier2_category_top_apps + tier3_remaining_apps_and_games
    print(f"📊 Scraper Priority Queue: {len(tier1_top_apps)} Main Top Apps & Games (Tier 1), {len(tier2_category_top_apps)} Category Top Hits (Tier 2), {len(tier3_remaining_apps_and_games)} Remaining Category-Wise Apps & Games (Tier 3). Total: {len(final_prioritized)} candidates.")
    return final_prioritized

def extract_app_metadata(app_url):
    try:
        resp = requests.get(app_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        html = resp.text

        # 1. App Title
        title_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        raw_title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else 'New App'
        app_title = re.sub(r'(?i)MOD\s*APK.*', '', raw_title).strip()
        if not app_title:
            app_title = raw_title

        # 2. App Logo Image (User Requirement: Square App Logo)
        logo_m = re.search(r'<div class=[\"\']app-info-icon[\"\'][^>]*>[\s\S]*?<img[^>]+src=[\"\']([^\"\']+)[\"\']', html)
        if logo_m:
            icon_raw = logo_m.group(1)
            # Remove thumbnail dimension suffix (e.g. -150x150) to get original full-res logo
            image_url = re.sub(r'-\d+x\d+(\.[a-zA-Z]+)$', r'\1', icon_raw)
        else:
            img_m = re.search(r'<meta property=[\"\']og:image[\"\'] content=[\"\']([^\"\']+)[\"\']', html)
            image_url = img_m.group(1) if img_m else None

        # 3. Short Description (Sanitized and capped to 220 chars for Telegram safety)
        desc_m = re.search(r'<meta property=[\"\']og:description[\"\'] content=[\"\']([^\"\']+)[\"\']', html)
        raw_desc = desc_m.group(1) if desc_m else ""
        short_desc = clean_text_for_telegram(raw_desc)
        if len(short_desc) > 220:
            short_desc = short_desc[:217] + "..."

        # 4. Info Bar Details (Version, Size, App Type)
        labels = re.findall(r'<div class="info-bar-label">\s*([^<]+)\s*</div>[\s\S]*?<div class="info-bar-value[^"]*">\s*([^<]+)\s*</div>', html)
        info = {l.strip().upper(): v.strip() for l, v in labels}
        version = info.get('VERSION', 'Latest')

        # Extract Real App Type from category link (info-bar-sub or breadcrumb)
        cat_match = re.search(r'<div class=[\"\']info-bar-sub[\"\'][^>]*>\s*<a[^>]+href=[\"\']https://liteapks\.com/(?:apps|games)/([a-zA-Z0-9\-_]+)[\"\'][^>]*>([^<]+)</a>', html)
        if not cat_match:
            cat_match = re.search(r'<a[^>]+href=[\"\']https://liteapks\.com/(?:apps|games)/([a-zA-Z0-9\-_]+)[\"\'][^>]*>([^<]+)</a>', html)

        slug = cat_match.group(1).lower() if cat_match else ""
        raw_cat = clean_text_for_telegram(cat_match.group(2).strip()) if cat_match else ""

        # Descriptive App Type resolution ("किस टाइप का ऐप है")
        app_type = APP_TYPE_MAPPING.get(slug)
        if not app_type and raw_cat:
            if '/games/' in html or 'game' in slug:
                app_type = f"{raw_cat} Game" if not raw_cat.lower().endswith('game') and not raw_cat.lower().endswith('games') else raw_cat
            else:
                app_type = f"{raw_cat} App" if not raw_cat.lower().endswith('app') and not raw_cat.lower().endswith('apps') else raw_cat
        if not app_type:
            app_type = "Android Game" if ('/games/' in html or 'game' in app_title.lower()) else "Android Application"

        # 5. Download Page Target
        dl_m = re.findall(r'href=[\"\'](https://liteapks\.com/download/[^\"\']+)[\"\']', html)
        if not dl_m:
            return None
        download_page = dl_m[0]

        # 6. Extract Specific MOD & Key Features
        features = []
        mod_tag_m = re.search(r'[\(\[](.*?)[\)\]]', raw_title)
        if mod_tag_m:
            for t in re.split(r'[,/|•+]', mod_tag_m.group(1)):
                clean_t = t.strip()
                if len(clean_t) > 1 and clean_t.lower() not in ['apk', 'mod']:
                    features.append(clean_t)

        headings = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html)
        ignore_words = ['liteapks', 'data safety', 'mod info', 'how to', 'faq', 'download', 'conclusion', 'overview', 'screenshots']
        for h in headings:
            clean_h = re.sub(r'<[^>]+>', '', h).strip()
            if clean_h and 4 <= len(clean_h) <= 50 and not any(w in clean_h.lower() for w in ignore_words):
                if clean_h.isupper():
                    clean_h = clean_h.title()
                if clean_h not in features:
                    features.append(clean_h)
            if len(features) >= 5:
                break

        if not features:
            features = ["VIP Premium Unlocked", "Ad-Free Experience", "All Features Active"]

        return {
            'app_url': app_url,
            'title': app_title,
            'image_url': image_url,
            'description': short_desc,
            'version': version,
            'category': app_type,
            'app_type': app_type,
            'features': features[:5],
            'download_page': download_page
        }
    except Exception as e:
        print(f"[Metadata Extraction Error] {app_url}: {e}")
        return None

def resolve_direct_apk_url(download_page_url):
    try:
        resp = requests.get(download_page_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        html = resp.text

        # Check sub-links (e.g. /1, /2 for versions)
        sub_m = re.findall(r'href=[\"\'](https://liteapks\.com/download/[^\"\']+/\d+)[\"\']', html)
        target_page = sub_m[0] if sub_m else download_page_url

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
            'file_ext': file_ext
        }
    except Exception as e:
        print(f"[Direct URL Resolution Error] {download_page_url}: {e}")
        return None

def upload_to_devuploads(apk_path, file_name):
    if not DEVUPLOADS_API_KEY:
        return None
    try:
        print(f"Uploading {file_name} to DevUploads...")
        srv_resp = requests.get(f"https://devuploads.com/api/upload/server?key={DEVUPLOADS_API_KEY}", timeout=15)
        if srv_resp.status_code != 200:
            return None
        srv_data = srv_resp.json()
        upload_url = srv_data.get('result')
        sess_id = srv_data.get('sess_id')
        if not upload_url or not sess_id:
            return None

        # Method 1: Try curl streaming
        try:
            cmd = [
                'curl.exe' if os.name == 'nt' else 'curl',
                '-s', '-X', 'POST', upload_url,
                '-F', f'sess_id={sess_id}',
                '-F', 'utype=reg',
                '-F', f'file_0=@{apk_path};filename={file_name}'
            ]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if p.returncode == 0 and p.stdout:
                res = json.loads(p.stdout)
                if res and isinstance(res, list) and res[0].get('file_code'):
                    code = res[0]['file_code']
                    print(f"✅ DevUploads Upload Success! Code: {code}")
                    return f"https://devuploads.com/{code}"
        except Exception as ce:
            print(f"[DevUploads Curl Warning]: {ce}")

        # Method 2: Fallback to requests
        with open(apk_path, 'rb') as f_up:
            r_up = requests.post(
                upload_url,
                data={'sess_id': sess_id, 'utype': 'reg'},
                files={'file_0': (file_name, f_up, 'application/vnd.android.package-archive')},
                timeout=600
            )
            res = r_up.json()
            if res and isinstance(res, list) and res[0].get('file_code'):
                code = res[0]['file_code']
                print(f"✅ DevUploads Upload Success! Code: {code}")
                return f"https://devuploads.com/{code}"
    except Exception as e:
        print(f"[DevUploads Upload Error]: {e}")
    return None

def cleanup_storage(specific_files=None):
    """Purges temporary APKs, images, and cache to guarantee zero storage bloat."""
    import gc
    if specific_files:
        for f in specific_files:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
                    print(f"[Storage Cleaned] Removed: {os.path.basename(f)}", flush=True)
            except Exception as e:
                pass

    # Auto-purge leftover APKs or temp files in workspace and temp dirs
    for check_dir in ['scratch', 'temp', 'tmp', '.']:
        if os.path.exists(check_dir):
            try:
                for fname in os.listdir(check_dir):
                    if fname.endswith(('.apk', '.apk.part', '.tmp', '.bin')) or fname.startswith('temp_'):
                        full_p = os.path.join(check_dir, fname)
                        if os.path.isfile(full_p):
                            try:
                                os.remove(full_p)
                                print(f"[Storage Cleaned] Auto-purged: {fname}", flush=True)
                            except Exception:
                                pass
            except Exception:
                pass

    gc.collect()

def generate_hashtags(meta):
    title = meta.get('title', '')
    cat = meta.get('category', '').lower()

    # Clean title hashtag (e.g. CapCut, Spotify, TikTok)
    clean_title = re.sub(r'[^a-zA-Z0-9]', '', title)
    clean_title = re.sub(r'[vV]\d+.*', '', clean_title)

    tags = []
    if clean_title:
        tags.append(f"#{clean_title}")

    # Dynamic app-specific category tags
    if any(w in cat for w in ['video', 'player', 'editor']):
        tags.extend(["#VideoEditor", "#VideoEditing", "#ReelsEditor"])
    elif any(w in cat for w in ['music', 'audio', 'song', 'sound']):
        tags.extend(["#MusicPlayer", "#AudioStreaming", "#MusicApp"])
    elif any(w in cat for w in ['photo', 'camera', 'image', 'picture']):
        tags.extend(["#PhotoEditor", "#Photography", "#CameraMod"])
    elif any(w in cat for w in ['game', 'action', 'rpg', 'arcade', 'racing']):
        tags.extend(["#AndroidGaming", "#ModGame", "#UnlimitedMoney"])
    elif any(w in cat for w in ['social', 'chat', 'media', 'network']):
        tags.extend(["#SocialApp", "#AndroidSocial"])
    elif any(w in cat for w in ['tool', 'utility', 'productivity']):
        tags.extend(["#AndroidTools", "#ProductivityApp"])
    else:
        cat_tag = re.sub(r'[^a-zA-Z0-9]', '', meta.get('category', ''))
        if cat_tag and cat_tag.lower() not in ['app', 'game']:
            tags.append(f"#{cat_tag}")

    # Core discovery tags
    tags.extend(["#ModAPK", "#Android", "#PremiumUnlocked", "#GetModPC"])

    # Remove duplicates preserving order
    seen = set()
    unique_tags = []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique_tags.append(t)

    return " ".join(unique_tags)

def post_apk_to_telegram(meta, apk_info, target_chat=None, is_update=False):
    chat = target_chat or CHANNEL_ID
    api_base = get_telegram_api_base()

    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Download Photo
        img_path = os.path.join(tmp_dir, "cover.jpg")
        if meta.get('image_url'):
            try:
                r_img = requests.get(meta['image_url'], headers=HEADERS, timeout=15)
                if r_img.status_code == 200:
                    with open(img_path, 'wb') as f:
                        f.write(r_img.content)
            except Exception as e:
                print(f"[Image Download Warning]: {e}")

        # 2. Download APK File
        ext = apk_info.get('file_ext', '.apk')
        clean_file_name = re.sub(r'[^a-zA-Z0-9\._\-]', '_', f"{meta['title']}_{meta['version']}{ext}")
        apk_path = os.path.join(tmp_dir, clean_file_name)

        print(f"Downloading APK: {clean_file_name} ({apk_info['size_mb']} MB)...")
        r_apk = requests.get(apk_info['download_url'], headers={'User-Agent': HEADERS['User-Agent'], 'Referer': apk_info['referer']}, stream=True, timeout=600)
        if r_apk.status_code != 200:
            print(f"[APK Download Failed]: HTTP {r_apk.status_code}")
            return False

        with open(apk_path, 'wb') as f:
            for chunk in r_apk.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

        print(f"Download complete! Preparing to publish...")

        # 3. DevUploads Mode (If DevUploads API key is configured)
        devuploads_url = upload_to_devuploads(apk_path, clean_file_name)
        if DEVUPLOADS_API_KEY and not devuploads_url:
            send_admin_alert(f"⚠️ <b>DevUploads Upload Failed</b>\nApp: {meta['title']} ({meta['version']})\nSize: {apk_info['size_mb']} MB\nLink: {meta.get('download_page', '')}")
        
        # Free disk storage immediately after upload
        cleanup_storage([apk_path])
        hashtags = generate_hashtags(meta)

        features_list = meta.get('features', ["VIP Premium Unlocked", "Ad-Free", "All Features Active"])
        features_text = "\n".join([f"• {f}" for f in features_list])
        update_badge = "🔄 <b>NEW UPDATE RELEASED!</b>\n\n" if is_update else ""

        ext = apk_info.get('file_ext', '.apk')
        is_xapk = ext in ['.xapk', '.zip', '.apks']
        pkg_type = ext.upper().replace('.', '')
        install_note = f"\n📦 <b>Package:</b> {pkg_type}\n💡 <i>Note: Install via XAPK Installer / extract OBB if needed.</i>" if is_xapk else ""

        if devuploads_url:
            header_part = (
                f"{update_badge}"
                f"📱 <b>{meta['title']}</b>\n\n"
                f"🏷️ <b>Version:</b> {meta['version']}\n"
                f"💾 <b>Size:</b> {apk_info['size_mb']} MB\n"
                f"📂 <b>Category:</b> {meta['app_type']}{install_note}\n\n"
                f"⚡ <b>MOD & Key Features:</b>\n"
                f"{features_text}\n\n"
            )
            footer_part = (
                f"📥 <b>Download {pkg_type} (High Speed):</b>\n"
                f"👉 {devuploads_url}\n\n"
                f"{hashtags}"
            )
            desc_raw = meta['description'] or 'Enjoy the latest version with full unlocked features on your Android device.'
            # Dynamically budget description so download link & tags are NEVER truncated
            allowed_desc_len = 1000 - len(header_part) - len(footer_part) - 25
            if allowed_desc_len < 60:
                allowed_desc_len = 60
            if len(desc_raw) > allowed_desc_len:
                desc_raw = desc_raw[:allowed_desc_len - 3] + "..."

            caption = f"{header_part}📖 <b>Description:</b>\n{desc_raw}\n\n{footer_part}\n"
            posted_success = False
            if os.path.exists(img_path):
                for attempt in range(2):
                    try:
                        with open(img_path, 'rb') as f_img:
                            r_photo = requests.post(
                                f"{api_base}/sendPhoto",
                                data={'chat_id': chat, 'caption': caption, 'parse_mode': 'HTML'},
                                files={'photo': f_img},
                                timeout=45
                            )
                            if r_photo.json().get('ok'):
                                print(f"✅ Successfully posted {meta['title']} (DevUploads link) to Telegram ({chat})!")
                                posted_success = True
                                break
                            else:
                                print(f"❌ Telegram sendPhoto (file) Error: {r_photo.json()}")
                    except Exception as e:
                        print(f"[Send Photo File Error attempt {attempt+1}]: {e}")
                        time.sleep(2)

                # Fallback: Send directly via image URL if file upload timed out/failed
                if not posted_success and meta.get('image_url'):
                    try:
                        r_url = requests.post(
                            f"{api_base}/sendPhoto",
                            data={'chat_id': chat, 'caption': caption, 'parse_mode': 'HTML', 'photo': meta['image_url']},
                            timeout=30
                        )
                        if r_url.json().get('ok'):
                            print(f"✅ Successfully posted {meta['title']} (photo URL fallback) to Telegram ({chat})!")
                            posted_success = True
                        else:
                            print(f"❌ Telegram sendPhoto (URL) Error: {r_url.json()}")
                    except Exception as e:
                        print(f"[Send Photo URL Error]: {e}")

            if not posted_success:
                try:
                    r_msg = requests.post(
                        f"{api_base}/sendMessage",
                        data={'chat_id': chat, 'text': caption, 'parse_mode': 'HTML'},
                        timeout=30
                    )
                    if r_msg.json().get('ok'):
                        print(f"✅ Successfully posted {meta['title']} (text fallback) to Telegram ({chat})!")
                        posted_success = True
                    else:
                        print(f"❌ Telegram sendMessage Error: {r_msg.json()}")
                except Exception as e:
                    print(f"[Send Message Error]: {e}")

            if not posted_success:
                send_admin_alert(f"⚠️ <b>Telegram Broadcast Failed</b>\nApp: {meta['title']} (v{meta['version']})\nTarget: {chat}\nCheck bot admin permissions in channel.")

            return posted_success

        # Fallback: Direct Telegram Document Upload (if DevUploads is not configured)
        caption = (
            f"📱 <b>{meta['title']}</b>\n\n"
            f"🏷️ <b>Version:</b> {meta['version']}\n"
            f"💾 <b>Size:</b> {apk_info['size_mb']} MB\n"
            f"📂 <b>Category:</b> {meta['app_type']}\n\n"
            f"⚡ <b>MOD & Key Features:</b>\n"
            f"{features_text}\n\n"
            f"📖 <b>Description:</b>\n"
            f"{meta['description'] or 'Enjoy the latest version with full unlocked features on your Android device.'}\n\n"
            f"{hashtags}"
        )

        if os.path.exists(img_path):
            try:
                with open(img_path, 'rb') as f_img:
                    requests.post(
                        f"{api_base}/sendPhoto",
                        data={'chat_id': chat, 'caption': caption, 'parse_mode': 'HTML'},
                        files={'photo': f_img},
                        timeout=30
                    )
            except Exception as e:
                print(f"[Send Photo Error]: {e}")

        doc_caption = f"📦 <b>{meta['title']}</b> ({meta['version']})\n<i>Direct APK File • {apk_info['size_mb']} MB</i>"
        try:
            cmd = [
                'curl.exe' if os.name == 'nt' else 'curl',
                '-s', '-X', 'POST', f"{api_base}/sendDocument",
                '-F', f"chat_id={chat}",
                '-F', f"caption={doc_caption}",
                '-F', "parse_mode=HTML",
                '-F', f"document=@{apk_path};filename={clean_file_name}"
            ]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=360)
            if p.returncode == 0 and p.stdout:
                res = json.loads(p.stdout)
                if res.get('ok'):
                    print(f"✅ Successfully posted {meta['title']} to Telegram (via curl streaming)!")
                    return True
        except Exception as e:
            print(f"[Curl Upload Info]: {e}")

        return False

def run_pipeline(demo_target=None):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting LiteAPKs Autonomous Pipeline...")
    cleanup_storage()
    sent_set = load_sent_history()
    print(f"Database contains {len(sent_set)} previously broadcasted APKs.")

    app_urls = scrape_latest_apk_links()
    print(f"Discovered {len(app_urls)} app links on LiteAPKs.")

    if len(app_urls) == 0:
        warn = "⚠️ <b>LiteAPKs Scraper Warning</b>: 0 app links discovered on LiteAPKs! Check site availability or HTML structure changes."
        print(warn)
        send_admin_alert(warn)

    processed_count = 0

    for url in app_urls:
        if processed_count >= MAX_APKS_PER_RUN:
            break

        # Fast pre-check: permanent ban on any LiteAPKs Store URL
        if is_banned_app("", url):
            continue

        meta = extract_app_metadata(url)
        if not meta:
            continue

        # Permanent ban check on app title / metadata
        if is_banned_app(meta.get('title', ''), url):
            print(f"🚫 [PERMANENTLY BANNED]: Skipping {meta['title']} ({url}) per user policy.")
            sent_set.add(url)
            sent_set.add(f"{url}::{meta.get('version', '')}")
            continue

        version = meta.get('version', 'Latest')
        version_key = f"{url}::{version}"

        # If this EXACT version has already been broadcasted, skip!
        if version_key in sent_set:
            continue

        # Check if this app was posted previously at an older version (Update release!)
        is_update = (url in sent_set)
        if is_update:
            print(f"🔄 [APP UPDATE DETECTED]: {meta['title']} has been updated to new version {version}!")
        else:
            print(f"\nProcessing New App: {meta['title']} ({url})")

        apk_info = resolve_direct_apk_url(meta['download_page'])
        if not apk_info:
            print(f"Could not resolve direct APK URL for {meta['title']}. Skipping.")
            continue

        print(f"Found APK: {meta['title']} (v{version}) | Size: {apk_info['size_mb']} MB")

        # File size safeguard: DevUploads provides unlimited cloud storage.
        # Allow large games/apps (up to 3000 MB / 3 GB) when DevUploads is configured.
        max_allowed_mb = int(os.getenv("MAX_UPLOAD_SIZE_MB", "3000")) if DEVUPLOADS_API_KEY else MAX_BOT_API_SIZE_MB
        if apk_info['size_mb'] > max_allowed_mb:
            print(f"⚠️ Skipping {meta['title']}: File size ({apk_info['size_mb']} MB) exceeds {max_allowed_mb} MB threshold.")
            if not DEVUPLOADS_API_KEY and not LOCAL_BOT_API_URL:
                sent_set.add(url)
                sent_set.add(version_key)
            continue

        success = False
        try:
            success = post_apk_to_telegram(meta, apk_info, target_chat=demo_target, is_update=is_update)
        except Exception as pe:
            print(f"[Pipeline Post Error] {meta['title']}: {pe}")
        finally:
            cleanup_storage()

        if success:
            sent_set.add(url)
            sent_set.add(version_key)
            processed_count += 1
            save_sent_history(sent_set)
            status_tag = "Updated" if is_update else "New"
            print(f"📊 Progress: [{processed_count}/{MAX_APKS_PER_RUN}] ({status_tag}) posted successfully.")
            # 3-second safe delay between posts to prevent Telegram 429 rate limit
            time.sleep(3)

    cleanup_storage()
    print(f"\nPipeline Run Completed. Broadcasted {processed_count} new/updated APKs.")

    # Automated GitHub Step Summary Report
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as sf:
                sf.write("### 🚀 LiteAPKs Agent Execution Summary\n\n")
                sf.write(f"- **Discovered Links**: {len(app_urls)}\n")
                sf.write(f"- **Broadcasted This Run**: {processed_count} / {MAX_APKS_PER_RUN}\n")
                sf.write(f"- **Total Sent Database**: {len(sent_set)} entries\n")
                sf.write(f"- **Execution Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
                sf.write(f"- **Status**: {'✅ Successful Run' if processed_count > 0 else 'ℹ️ No New Updates'}\n")
        except Exception as se:
            print(f"[Step Summary Error]: {se}")

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
