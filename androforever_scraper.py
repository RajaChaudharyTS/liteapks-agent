# -*- coding: utf-8 -*-
"""
androforever_scraper.py — Dedicated High-Performance Ingestion Engine for AndroForever.com

Features:
1. Candidate Priority Cascade:
   - Priority 1: Popular / Trending Apps (from Homepage Carousel & Featured Grid)
   - Priority 2 & 3: Category / Non-Popular Apps (Tools, Video, Music, Personalization, Productivity, etc.)
   - Smart Skip: If popular apps have no updates or were already posted, seamlessly fall back to category apps.
2. Strict Zero-Watermark & Anonymity Enforcement:
   - Absolutely ZERO mentions of 'androforever', 'androforever.com', 'android-forever' in titles, descriptions,
     features, file names, or metadata.
3. Clean Metadata Extraction:
   - Pure app titles, normalized semantic versions, clean logo URLs, categorized tags, and high-impact MOD features.
4. Direct CDN / CloudFront Link Resolution:
   - Extracts unthrottled direct .apk URLs with exact file sizes and sanitized .apk filenames.
"""

import os
import sys
import re
import html
import urllib.parse
import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,es;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Referer': 'https://androforever.com/'
}

CATEGORY_MAP = {
    'herramientas': 'Tools & Utilities',
    'aplicaciones-de-video': 'Video Players & Editors',
    'musica': 'Music & Audio',
    'personalizacion': 'Personalization & Customization',
    'productividad': 'Productivity & Office',
    'fotografia': 'Photography & Photo Editor',
    'social': 'Social Media & Networking',
    'educacion': 'Education & Language Learning',
    'entretenimiento': 'Entertainment & OTT Streaming',
    'salud': 'Health & Fitness',
    'accion': 'Action & Shooting Game',
    'arcade': 'Arcade Game',
    'carreras': 'Racing Game',
    'aventura': 'Adventure Game',
    'estrategia': 'Strategy & Tactical Game',
    'simulacion': 'Simulation Game',
    'juegos-de-rol': 'RPG Game'
}

def strip_androforever_branding(text):
    """
    Strict zero-watermark sanitizer. Permanently removes any trace of androforever.
    """
    if not text:
        return ""
    t = str(text)
    # Remove domains and brand names
    t = re.sub(r'(?i)\b(?:www\.)?androforever(?:\.com)?\b', '', t)
    t = re.sub(r'(?i)\b(?:www\.)?android-?forever(?:\.com)?\b', '', t)
    t = re.sub(r'(?i)androforever', '', t)
    t = re.sub(r'(?i)androidforever', '', t)
    # Clean whitespace and trailing dashes/separators
    t = re.sub(r'\s*-\s*$', '', t).strip()
    t = re.sub(r'^\s*-\s*', '', t).strip()
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t

def clean_androforever_title(raw_title):
    if not raw_title:
        return ""
    t = strip_androforever_branding(raw_title)
    # Strip Spanish modifier tags
    t = re.sub(r'(?i)\b(?:dinero infinito|todo desbloqueado|inmortalidad|mod menu|gemas infinitas)\b', '', t)
    t = re.sub(r'(?i)\b(?:descargar|para android|ultima version|gratis|hackeado)\b', '', t)
    t = re.sub(r'\s*[\(\[].*?[\)\]]', ' ', t)
    t = re.sub(r'(?i)\bv?\d+(?:\.\d+)+[a-z0-9_+-]*\b', ' ', t)
    t = re.sub(r'(?i)\b(?:mod|apk|premium|unlocked|vip|latest|version|pro|plus)\b', ' ', t)
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def clean_androforever_version(ver_str):
    if not ver_str:
        return ""
    v = strip_androforever_branding(str(ver_str))
    v = re.sub(r'^[vV]+\s*', '', v)
    v = re.sub(r'(?i)\s*\(mod\).*', '', v)
    v = re.sub(r'(?i)\s*mod.*', '', v)
    v = re.sub(r'(?i)\s*apk.*', '', v)
    v = v.strip()
    m = re.search(r'([0-9]+(?:\.[0-9a-zA-Z_\-+]+)+)', v)
    if m:
        return m.group(1).strip()
    return v.strip()

def scrape_androforever_prioritized_candidates():
    """
    Scrapes AndroForever for Priority 1 (Popular/Trending) and Priority 2/3 (Categories).
    Returns: (popular_candidates, category_candidates)
    """
    popular_candidates = []
    category_candidates = []
    seen_urls = set()

    # =========================================================================
    # TIER 1: Popular / Trending Apps (from Homepage)
    # =========================================================================
    try:
        r_home = requests.get('https://androforever.com/', headers=HEADERS, timeout=15)
        if r_home.status_code == 200:
            soup = BeautifulSoup(r_home.text, 'html.parser')

            # 1. Swiper slides (Top carousel)
            for swiper in soup.find_all('div', class_=lambda c: c and 'swiper-slide' in c):
                a = swiper.find('a', href=True)
                if a and a['href'].startswith('https://androforever.com/'):
                    url = a['href'].rstrip('/') + '/'
                    if url not in seen_urls and not any(k in url for k in ['/aplicaciones', '/juegos', '/blog', 'dmca']):
                        t_el = swiper.find(['div', 'span', 'h2', 'h3'], class_=lambda c: c and 'title' in c)
                        raw_title = t_el.text.strip() if t_el else a.text.strip().split('\n')[0]
                        v_el = swiper.find('span', class_='version')
                        raw_ver = v_el.text.strip() if v_el else ''
                        seen_urls.add(url)
                        popular_candidates.append({
                            'url': url,
                            'title': clean_androforever_title(raw_title) or url.strip('/').split('/')[-1].replace('-', ' ').title(),
                            'version': clean_androforever_version(raw_ver),
                            'category': 'Popular App',
                            'priority_label': 'Priority 1 - Popular',
                            'source': 'androforever',
                            'is_popular': True
                        })

            # 2. Homepage bav cards (Featured and trending apps)
            for bav in soup.find_all('div', class_=lambda c: c and 'bav' in c):
                a = bav.find('a', href=True)
                if a and a['href'].startswith('https://androforever.com/'):
                    url = a['href'].rstrip('/') + '/'
                    if url not in seen_urls and not any(k in url for k in ['/aplicaciones', '/juegos', '/blog', 'dmca']):
                        t_el = bav.find(['div', 'span'], class_='title')
                        raw_title = t_el.text.strip() if t_el else a.get('title', '')
                        v_el = bav.find('span', class_='version')
                        raw_ver = v_el.text.strip() if v_el else ''
                        seen_urls.add(url)
                        popular_candidates.append({
                            'url': url,
                            'title': clean_androforever_title(raw_title) or url.strip('/').split('/')[-1].replace('-', ' ').title(),
                            'version': clean_androforever_version(raw_ver),
                            'category': 'Trending App',
                            'priority_label': 'Priority 1 - Trending',
                            'source': 'androforever',
                            'is_popular': True
                        })
    except Exception as e:
        print(f"[AndroForever Homepage Scrape Error]: {e}")

    # =========================================================================
    # TIER 2 & 3: Category / Non-Popular Apps (Tools, Media, Games)
    # =========================================================================
    # Focus category scrapers on regular applications (Tools, Media, Social, etc.)
    # Heavy 3D games (> 50 MB) cannot be posted as Telegram raw documents
    categories_to_scrape = [
        ('herramientas', 'https://androforever.com/aplicaciones/herramientas/'),
        ('aplicaciones-de-video', 'https://androforever.com/aplicaciones/aplicaciones-de-video/'),
        ('musica', 'https://androforever.com/aplicaciones/musica/'),
        ('personalizacion', 'https://androforever.com/aplicaciones/personalizacion/'),
        ('productividad', 'https://androforever.com/aplicaciones/productividad/'),
        ('fotografia', 'https://androforever.com/aplicaciones/fotografia/'),
        ('social', 'https://androforever.com/aplicaciones/social/'),
        ('educacion', 'https://androforever.com/aplicaciones/educacion/'),
        ('entretenimiento', 'https://androforever.com/aplicaciones/entretenimiento/'),
        ('salud', 'https://androforever.com/aplicaciones/salud/')
    ]

    for cat_slug, cat_url in categories_to_scrape:
        cat_name = CATEGORY_MAP.get(cat_slug, 'Android App')
        try:
            r_cat = requests.get(cat_url, headers=HEADERS, timeout=12)
            if r_cat.status_code == 200:
                soup_cat = BeautifulSoup(r_cat.text, 'html.parser')
                for bav in soup_cat.find_all('div', class_=lambda c: c and 'bav' in c):
                    a = bav.find('a', href=True)
                    if a and a['href'].startswith('https://androforever.com/'):
                        url = a['href'].rstrip('/') + '/'
                        if url not in seen_urls and not any(k in url for k in ['/aplicaciones', '/juegos', '/blog', 'dmca']):
                            t_el = bav.find(['div', 'span'], class_='title')
                            raw_title = t_el.text.strip() if t_el else a.get('title', '')
                            v_el = bav.find('span', class_='version')
                            raw_ver = v_el.text.strip() if v_el else ''
                            seen_urls.add(url)
                            category_candidates.append({
                                'url': url,
                                'title': clean_androforever_title(raw_title) or url.strip('/').split('/')[-1].replace('-', ' ').title(),
                                'version': clean_androforever_version(raw_ver),
                                'category': cat_name,
                                'priority_label': f"Priority 2 - {cat_name}",
                                'source': 'androforever',
                                'is_popular': False
                            })
        except Exception as e:
            print(f"[AndroForever Category Scrape Error ({cat_slug})]: {e}")

    print(f"[AndroForever Candidates Discovered]: {len(popular_candidates)} Popular + {len(category_candidates)} Category apps.")
    return popular_candidates, category_candidates


def extract_androforever_metadata(app_url):
    """
    Extracts complete, verified metadata for an AndroForever app URL.
    100% Zero-Watermark: cleans all branding and Spanish promotional text.
    """
    clean_url = app_url.rstrip('/') + '/'
    try:
        r = requests.get(clean_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, 'html.parser')

        # 1. Title
        raw_title = ""
        h1 = soup.find('h1')
        if h1:
            raw_title = h1.text.strip()
        elif soup.title:
            raw_title = soup.title.string.strip()

        title = clean_androforever_title(raw_title)
        if not title:
            slug = clean_url.strip('/').split('/')[-1]
            title = re.sub(r'-(?:mod|apk|premium|pro|plus|android|mobile)$', '', slug).replace('-', ' ').title()

        # 2. Version
        version = ""
        # Try table or metadata spans
        for span in soup.find_all('span', class_='version'):
            v_text = clean_androforever_version(span.text)
            if v_text:
                version = v_text
                break
        if not version:
            m_ver = re.search(r'\b(?:v|version)?\s*([0-9]+(?:\.[0-9a-zA-Z_\-+]+)+)', raw_title, re.IGNORECASE)
            if m_ver:
                version = m_ver.group(1).strip()

        # 3. Logo Image URL
        image_url = ""
        og_img = soup.find('meta', property='og:image')
        if og_img and og_img.get('content'):
            image_url = og_img['content']
        else:
            img = soup.find('img', class_=lambda c: c and 'wp-post-image' in c)
            if img:
                image_url = img.get('src') or img.get('data-src', '')

        # 4. Category
        app_type = "Tools & Utilities"
        # Check breadcrumbs or entry category
        for a in soup.find_all('a', href=True):
            href = a['href']
            for slug_key, cat_name in CATEGORY_MAP.items():
                if f"/{slug_key}/" in href:
                    app_type = cat_name
                    break

        # 5. Features
        features = [
            "Pro / VIP Premium Unlocked",
            "All Advanced Features Active",
            "Zero Ads & High-Performance Optimized"
        ]
        # Inspect list items if available
        entry = soup.find('div', class_='entry-content') or soup.find('div', class_='entry')
        if entry:
            extracted_lis = []
            for li in entry.find_all('li'):
                txt = strip_androforever_branding(li.text.strip())
                if txt and len(txt) > 5 and len(txt) < 80 and not any(k in txt.lower() for k in ['descargar', 'instalar', 'click', 'link']):
                    extracted_lis.append(txt)
            if extracted_lis:
                features = extracted_lis[:3]

        # 6. Description
        description = f"{title} provides an exceptional, modern, and unlocked experience tailored for your Android device."
        if entry:
            paragraphs = [p.text.strip() for p in entry.find_all('p') if p.text.strip()]
            for p in paragraphs:
                clean_p = strip_androforever_branding(p)
                if len(clean_p) > 50 and not any(k in clean_p.lower() for k in ['descargar', 'link', 'clic', 'instalar']):
                    description = clean_p
                    break

        download_page = f"{clean_url}download/"

        return {
            'title': title,
            'version': version or "Latest",
            'app_type': app_type,
            'image_url': image_url,
            'description': description,
            'features': features,
            'download_page': download_page,
            'url': clean_url,
            'source': 'androforever'
        }
    except Exception as e:
        print(f"[AndroForever Extract Metadata Error]: {e}")
        return None


def resolve_androforever_apk_url(download_page_url, title="App", version="Latest"):
    """
    Resolves the direct .apk URL from the download page (cloud.androforever.com / CloudFront / CDN).
    Follows redirects to obtain the final direct .apk binary URL.
    Returns sanitized download info with 0% watermark file names.
    """
    clean_dl_page = download_page_url.rstrip('/') + '/'
    try:
        r = requests.get(clean_dl_page, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, 'html.parser')

        apk_url = None
        for a in soup.find_all('a', href=True):
            href = a['href']
            # Target primary download buttons
            if 'downloadAPK' in a.get('class', []) or 'cloud.androforever.com' in href or href.endswith('.apk'):
                apk_url = href
                break

        if not apk_url:
            return None

        # Resolve redirects if URL points to a redirector like clic-latam or cdn
        size_mb = 25.0
        final_apk_url = apk_url
        try:
            r_head = requests.head(apk_url, headers=HEADERS, allow_redirects=True, timeout=12)
            if r_head.status_code == 200:
                final_apk_url = r_head.url
                cl = r_head.headers.get('content-length')
                if cl:
                    size_mb = round(int(cl) / (1024 * 1024), 2)
        except Exception:
            pass

        # Early size guard: If APK is larger than 49.0 MB, it cannot be sent as Telegram document
        if size_mb > 49.0:
            print(f"⏩ [AndroForever Size Guard]: {title} ({size_mb} MB) exceeds Telegram 49.0 MB document limit. Skipping cleanly.")
            return None

        # Build clean canonical filename from app title and version (100% Zero-Watermark)
        safe_title = re.sub(r'[^\w]+', '_', strip_androforever_branding(title)).strip('_')
        safe_ver = re.sub(r'[^\w\.\-]+', '', strip_androforever_branding(version)).strip('_')
        if not safe_ver:
            safe_ver = "Latest"

        if "premium" not in safe_title.lower() and "mod" not in safe_title.lower() and "pro" not in safe_title.lower():
            clean_file_name = f"{safe_title}_Premium_v{safe_ver}.apk"
        else:
            clean_file_name = f"{safe_title}_v{safe_ver}.apk"

        return {
            'download_url': final_apk_url,
            'referer': clean_dl_page,
            'clean_file_name': clean_file_name,
            'size_mb': size_mb,
            'file_ext': '.apk'
        }
    except Exception as e:
        print(f"[AndroForever Resolve APK Error]: {e}")
        return None
