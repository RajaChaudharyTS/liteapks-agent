import os
import sys
import time
import json
import html
import re
import queue
import secrets
import threading
import urllib.parse
import requests

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

import agent
import androforever_scraper
try:
    from config import DEVUPLOADS_API_KEY, MAX_UPLOAD_SIZE_MB
except Exception:
    from config import DEVUPLOADS_API_KEY
    MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "4000"))

# ==============================================================================
# 🤖 BOT CREDENTIALS & CHANNELS
# ==============================================================================
BOT_TOKEN = "9067213510:AAFx-KCh_fikr4ET8i3mcijxcK0iKkcRImk"
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
BOT_USERNAME = "FileShareAppWishBot"

ADMIN_CHAT_ID = "669644885"

# Channels
CHANNEL_GETMOD = "-1002359898832"        # Official GetMod Channel
CHANNEL_GETMOD_HANDLE = "@AndroModApps"
CHANNEL_DEALS = "@DealsFetcher"            # Official DealsFetcher Channel
CHANNEL_DEALS_HANDLE = "@DealsFetcher"

# Databases
USERS_DB_PATH = os.path.join(CURRENT_DIR, "wishlist_users.json")
FILE_STORE_PATH = os.path.join(CURRENT_DIR, "wishlist_file_store.json")
CATALOG_PATH = os.path.join(CURRENT_DIR, "apps_catalog.json")

# Concurrency & Locks
db_lock = threading.Lock()
scrape_queue = queue.Queue()
active_scrapes = {}
pending_confirmations = {}
confirmations_lock = threading.Lock()

# ==============================================================================
# 📦 DATABASE LOADERS & SAVERS
# ==============================================================================
def load_json_db(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[DB Load Error] {path}: {e}")
    return default

def save_json_db(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[DB Save Error] {path}: {e}")

def load_users():
    return load_json_db(USERS_DB_PATH, {})

def save_users(data):
    save_json_db(USERS_DB_PATH, data)

def load_files():
    return load_json_db(FILE_STORE_PATH, {})

def save_files(data):
    save_json_db(FILE_STORE_PATH, data)

# ==============================================================================
# 🌐 TELEGRAM API CLIENT HELPERS
# ==============================================================================
def send_tg(method, payload=None, files=None):
    try:
        url = f"{API_BASE}/{method}"
        res = requests.post(url, data=payload, files=files, timeout=30)
        data = res.json()
        if not data.get('ok'):
            print(f"[TG Error] {method}: {data.get('description')}")
        return data
    except Exception as e:
        print(f"[TG Exception] {method}: {e}")
        return {}

def send_msg(chat_id, text, reply_markup=None):
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    return send_tg('sendMessage', payload)

def send_photo_card(chat_id, photo_url, caption, reply_markup=None):
    payload = {
        'chat_id': chat_id,
        'caption': caption,
        'parse_mode': 'HTML'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    if photo_url:
        payload['photo'] = photo_url
        res = send_tg('sendPhoto', payload)
        if res.get('ok'):
            return res
    # Fallback to text message if photo fails
    return send_msg(chat_id, caption, reply_markup)

def answer_callback(cb_id, text=None, show_alert=False):
    payload = {
        'callback_query_id': cb_id,
        'show_alert': 'true' if show_alert else 'false'
    }
    if text:
        payload['text'] = text
    return send_tg('answerCallbackQuery', payload)

# ==============================================================================
# 🔒 CHANNEL MEMBERSHIP VERIFIER
# ==============================================================================
def is_member_of_channel(channel_id, user_id):
    if str(user_id) == str(ADMIN_CHAT_ID):
        return True
    try:
        res = send_tg('getChatMember', {'chat_id': channel_id, 'user_id': user_id})
        if res.get('ok'):
            status = res.get('result', {}).get('status', '')
            return status in ['creator', 'administrator', 'member', 'restricted']
        else:
            err = res.get('description', '')
            if 'chat not found' in err or 'bot is not a member' in err:
                print(f"[Warning] Bot is not yet added to channel {channel_id}. Please add @{BOT_USERNAME} as Administrator!")
                return True # Temporary pass during setup so user is not permanently blocked
    except Exception as e:
        print(f"[Channel Membership Error]: {e}")
    return False

# ==============================================================================
# 👤 USER RETENTION & QUOTA GATEKEEPER (DAY 1 vs DAY 2+)
# ==============================================================================
def get_user_state(user_id):
    today = time.strftime("%Y-%m-%d")
    uid = str(user_id)
    with db_lock:
        users = load_users()
        if uid not in users:
            users[uid] = {
                'user_id': user_id,
                'first_seen': today,
                'day_1_completed': False,
                'day_1_apps': 0,
                'daily_date': today,
                'daily_apps': 0,
                'lifetime_apps': 0,
                'last_active': time.time()
            }
            save_users(users)
            return users[uid]

        user = users[uid]
        # Day transition check
        if user.get('daily_date') != today:
            user['daily_date'] = today
            user['daily_apps'] = 0
            user['day_1_completed'] = True  # Automatically move to Day 2+ rules
            save_users(users)
        return user

def verify_user_request_eligibility(user_id):
    """
    Evaluates strict multi-tier progression:
    Day 1:
      - App 1: 100% Free
      - App 2: Must join @DealsFetcher
      - App 3: Must join @AndroModApps
      - App 4+: Day 1 limit reached (Max 3 apps)
    Day 2 Onwards (Daily 2 Apps):
      - Daily App 1: Must be active member of @DealsFetcher
      - Daily App 2: Must be active member of @AndroModApps
      - Daily App 3+: Daily limit reached (Max 2 apps)
    """
    user = get_user_state(user_id)
    today = time.strftime("%Y-%m-%d")
    is_day_1 = (not user.get('day_1_completed')) and (user.get('first_seen') == today)

    if is_day_1:
        d1_count = user.get('day_1_apps', 0)
        if d1_count == 0:
            return 'ALLOWED', 'day1_app1', None
        elif d1_count == 1:
            if not is_member_of_channel(CHANNEL_DEALS, user_id):
                return 'NEED_CHANNEL', 'day1_app2', {
                    'channel': CHANNEL_DEALS_HANDLE,
                    'name': 'Deals Fetcher',
                    'url': f'https://t.me/{CHANNEL_DEALS_HANDLE.lstrip("@")}',
                    'title': 'Deals Fetcher Official'
                }
            return 'ALLOWED', 'day1_app2', None
        elif d1_count == 2:
            if not is_member_of_channel(CHANNEL_GETMOD, user_id):
                return 'NEED_CHANNEL', 'day1_app3', {
                    'channel': CHANNEL_GETMOD_HANDLE,
                    'name': 'GETMOD Official',
                    'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}',
                    'title': 'GETMOD PC & Android'
                }
            return 'ALLOWED', 'day1_app3', None
        else:
            return 'LIMIT_REACHED', 'day1_limit', (
                "🎉 <b>Day 1 Welcome Quota Fulfilled (3/3 Apps Received)!</b>\n\n"
                "Come back tomorrow to request more apps! Your regular daily quota (2 Apps/Day) starts tomorrow.\n\n"
                f"👉 <i>Stay connected to {CHANNEL_GETMOD_HANDLE} and {CHANNEL_DEALS_HANDLE} for daily updates!</i>"
            )

    else:
        # Day 2 Onwards
        daily_count = user.get('daily_apps', 0)
        if daily_count == 0:
            if not is_member_of_channel(CHANNEL_DEALS, user_id):
                return 'NEED_CHANNEL', 'daily_app1', {
                    'channel': CHANNEL_DEALS_HANDLE,
                    'name': 'Deals Fetcher',
                    'url': f'https://t.me/{CHANNEL_DEALS_HANDLE.lstrip("@")}',
                    'title': 'Deals Fetcher Official'
                }
            return 'ALLOWED', 'daily_app1', None
        elif daily_count == 1:
            if not is_member_of_channel(CHANNEL_GETMOD, user_id):
                return 'NEED_CHANNEL', 'daily_app2', {
                    'channel': CHANNEL_GETMOD_HANDLE,
                    'name': 'GETMOD Official',
                    'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}',
                    'title': 'GETMOD PC & Android'
                }
            return 'ALLOWED', 'daily_app2', None
        else:
            return 'LIMIT_REACHED', 'daily_limit', (
                "⛔ <b>Daily Limit Reached (2/2 Apps)!</b>\n\n"
                "You have used your 2 free app requests for today. Please come back tomorrow to request more apps!\n\n"
                f"👉 <i>Explore hundreds of modded apps on {CHANNEL_GETMOD_HANDLE} in the meantime!</i>"
            )

def record_app_delivered(user_id, stage):
    uid = str(user_id)
    with db_lock:
        users = load_users()
        if uid in users:
            u = users[uid]
            u['lifetime_apps'] = u.get('lifetime_apps', 0) + 1
            u['last_active'] = time.time()
            if stage.startswith('day1_'):
                u['day_1_apps'] = u.get('day_1_apps', 0) + 1
                u['daily_apps'] = u.get('daily_apps', 0) + 1
                if u['day_1_apps'] >= 3:
                    u['day_1_completed'] = True
            else:
                u['daily_apps'] = u.get('daily_apps', 0) + 1
            save_users(users)

# ==============================================================================
# ☁️ MODE A: CLOUD FILE SHARING ENGINE (2 GB Cloud Drive)
# ==============================================================================
def handle_incoming_file(msg, from_user):
    chat_id = msg.get('chat', {}).get('id')
    doc = msg.get('document')
    vid = msg.get('video')
    aud = msg.get('audio')
    pho = msg.get('photo')
    voice = msg.get('voice')

    file_type = None
    file_id = None
    file_name = "File"
    file_size = 0
    caption = msg.get('caption', '')

    if doc:
        file_type = 'document'
        file_id = doc.get('file_id')
        file_name = doc.get('file_name', 'Document')
        file_size = doc.get('file_size', 0)
    elif vid:
        file_type = 'video'
        file_id = vid.get('file_id')
        file_name = vid.get('file_name', 'Video.mp4')
        file_size = vid.get('file_size', 0)
    elif aud:
        file_type = 'audio'
        file_id = aud.get('file_id')
        file_name = aud.get('file_name', 'Audio.mp3')
        file_size = aud.get('file_size', 0)
    elif pho:
        file_type = 'photo'
        file_id = pho[-1].get('file_id')
        file_name = 'Photo.jpg'
        file_size = pho[-1].get('file_size', 0)
    elif voice:
        file_type = 'voice'
        file_id = voice.get('file_id')
        file_name = 'Voice.ogg'
        file_size = voice.get('file_size', 0)
    else:
        return

    size_mb = round(file_size / (1024 * 1024), 2)
    file_code = secrets.token_urlsafe(6)

    with db_lock:
        store = load_files()
        store[file_code] = {
            'file_id': file_id,
            'file_type': file_type,
            'file_name': file_name,
            'file_size_mb': size_mb,
            'caption': caption,
            'uploader_id': from_user.get('id'),
            'uploader_name': from_user.get('first_name', ''),
            'created_at': time.time(),
            'downloads_count': 0
        }
        save_files(store)

    share_link = f"https://t.me/{BOT_USERNAME}?start=file_{file_code}"
    card_text = (
        f"✅ <b>File Stored in High-Speed Cloud Storage!</b> ☁️⚡\n\n"
        f"📁 <b>Filename:</b> <code>{html.escape(file_name)}</code>\n"
        f"💾 <b>Size:</b> <b>{size_mb} MB</b>\n"
        f"⚡ <b>Delivery:</b> Instant Telegram Cloud CDN\n\n"
        f"🔗 <b>Your Permanent Shareable Link:</b>\n"
        f"👉 <code>{share_link}</code>\n\n"
        f"💡 <i>Share this link anywhere! Anyone who clicks can download this file instantly.</i>"
    )
    encoded_name = urllib.parse.quote(file_name)
    share_button_url = f"https://t.me/share/url?url={share_link}&text=Download%20{encoded_name}%20via%20GETMOD%20Cloud"
    keyboard = {
        'inline_keyboard': [
            [{'text': '📤 Share Link with Friends', 'url': share_button_url}],
            [{'text': f'📢 Join Channel {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'}]
        ]
    }
    send_msg(chat_id, card_text, keyboard)

def handle_shared_file_delivery(chat_id, user_id, file_code):
    with db_lock:
        store = load_files()
        record = store.get(file_code)

    if not record:
        send_msg(chat_id, "❌ <b>File Not Found!</b>\n\nThis file link is invalid or may have been deleted.")
        return

    # Check Channel Membership before delivering file
    if not is_member_of_channel(CHANNEL_GETMOD, user_id):
        fname = html.escape(record.get('file_name', 'File'))
        fsize = record.get('file_size_mb', 0)
        lock_msg = (
            f"🔒 <b>File Locked! (Channel Subscription Required)</b>\n\n"
            f"📁 <b>Filename:</b> <code>{fname}</code>\n"
            f"💾 <b>Size:</b> <b>{fsize} MB</b>\n\n"
            f"⚠️ <i>To download this file, please join our official channel <b>{CHANNEL_GETMOD_HANDLE}</b> first!</i>\n\n"
            f"After joining, tap the <b>'🔄 Verify & Download'</b> button below:"
        )
        keyboard = {
            'inline_keyboard': [
                [{'text': f'📢 Join {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'}],
                [{'text': '🔄 Verify & Download', 'callback_data': f'verify_file_{file_code}'}]
            ]
        }
        send_msg(chat_id, lock_msg, keyboard)
        return

    # Send the stored file
    send_msg(chat_id, "🚀 <i>Delivering your file via high-speed cloud CDN...</i>")
    f_type = record.get('file_type')
    f_id = record.get('file_id')
    raw_caption = record.get('caption', '')
    caption = f"{html.escape(raw_caption)}\n\n⚡ <i>Delivered via @{BOT_USERNAME}</i>" if raw_caption else f"📁 <b>{html.escape(record.get('file_name', 'File'))}</b> ({record.get('file_size_mb', 0)} MB)\n⚡ <i>Delivered via @{BOT_USERNAME}</i>"

    payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
    if f_type == 'document':
        payload['document'] = f_id
        send_tg('sendDocument', payload)
    elif f_type == 'video':
        payload['video'] = f_id
        send_tg('sendVideo', payload)
    elif f_type == 'audio':
        payload['audio'] = f_id
        send_tg('sendAudio', payload)
    elif f_type == 'photo':
        payload['photo'] = f_id
        send_tg('sendPhoto', payload)
    elif f_type == 'voice':
        payload['voice'] = f_id
        send_tg('sendVoice', payload)
    else:
        payload['document'] = f_id
        send_tg('sendDocument', payload)

    with db_lock:
        store = load_files()
        if file_code in store:
            store[file_code]['downloads_count'] = store[file_code].get('downloads_count', 0) + 1
            save_files(store)

# ==============================================================================
# 📱 MODE B: ON-DEMAND APP WISHLIST ENGINE (Cache-First + Live Scrape)
# ==============================================================================
def build_app_delivery_card(app_data, is_user_request=False):
    title = html.escape(app_data.get('title', 'Requested App'))
    ver = html.escape(str(app_data.get('version', 'Latest')))
    cat = html.escape(app_data.get('category', 'Android Mod'))
    size = app_data.get('size_mb', 'N/A')
    dev_url = app_data.get('devuploads_url', '')

    tag_line = "🎯 <b>#UserRequest | Community Demand Fulfilled!</b> ✨\n\n" if is_user_request else ""

    caption = (
        f"{tag_line}"
        f"📱 <b>{title}</b>\n\n"
        f"🏷️ <b>Version:</b> <code>v{ver}</code>\n"
        f"📂 <b>Category:</b> {cat}\n"
        f"💾 <b>File Size:</b> <b>{size} MB</b>\n"
        f"⚡ <b>Mod Info:</b> Premium Unlocked • Ad-Free • 100% Tested\n\n"
        f"📥 <b>High-Speed Download Link:</b>\n"
        f"👉 <a href='{dev_url}'><b>Click Here to Download via DevUploads</b></a>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 <b>Official Channels:</b>\n"
        f"• {CHANNEL_GETMOD_HANDLE} (Daily Modded Apps & Games)\n"
        f"• {CHANNEL_DEALS_HANDLE} (Exclusive Loot Deals & Coupons)\n\n"
        f"⚡ <i>Delivered on-demand by @{BOT_USERNAME}</i>"
    )
    keyboard = {
        'inline_keyboard': [
            [{'text': '🚀 Fast Download Link (DevUploads)', 'url': dev_url}],
            [
                {'text': f'📢 {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'},
                {'text': f'🔥 {CHANNEL_DEALS_HANDLE}', 'url': f'https://t.me/{CHANNEL_DEALS_HANDLE.lstrip("@")}'}
            ]
        ]
    }
    return caption, keyboard

def deliver_catalog_app(chat_id, user_id, app_slug, app_data, stage):
    caption, keyboard = build_app_delivery_card(app_data, is_user_request=False)
    img_url = app_data.get('image_url') or app_data.get('icon_url')
    send_photo_card(chat_id, img_url, caption, keyboard)
    record_app_delivered(user_id, stage)
    print(f"[Delivered from Catalog] App '{app_data.get('title')}' delivered to user {user_id} ({stage}).")


def find_app_and_request_confirmation_worker(chat_id, user_id, query_text, stage, user_name='Valued Member'):
    try:
        # White-labeled search notice (Strictly NO LiteAPKs or cloud repo mention)
        send_msg(
            chat_id,
            f"""🔍 <b>Searching for '{html.escape(query_text)}'...</b>

⚡ <i>Checking mod servers & database...</i>"""
        )

        # 1. Check local catalog cache first
        matched_app = None
        try:
            catalog = agent.load_apps_catalog()
            norm_q = re.sub(r'[^a-z0-9]+', '', query_text.lower())
            for slug, item in catalog.items():
                norm_slug = re.sub(r'[^a-z0-9]+', '', slug.lower())
                norm_title = re.sub(r'[^a-z0-9]+', '', item.get('title', '').lower())
                if norm_q in norm_slug or norm_q in norm_title or norm_slug in norm_q:
                    matched_app = item
                    break
        except Exception as e:
            print(f"[Catalog Search Error]: {e}")

        dev_link = matched_app.get('devuploads_url', '') if matched_app else ''
        if matched_app and dev_link and str(dev_link).startswith('https://devuploads.com/'):
            session_id = secrets.token_hex(6)
            with confirmations_lock:
                pending_confirmations[session_id] = {
                    'source': 'DATABASE',
                    'chat_id': chat_id,
                    'user_id': user_id,
                    'stage': stage,
                    'user_name': user_name,
                    'app_data': matched_app,
                    'query': query_text,
                    'created_at': time.time(),
                    'processing': False
                }

            title = html.escape(matched_app.get('title', query_text))
            ver = html.escape(str(matched_app.get('version', 'Latest')))
            cat = html.escape(matched_app.get('category', 'Android Mod'))
            size = matched_app.get('size_mb', 'N/A')

            confirm_msg = f"""✅ <b>{title}</b> (v{ver}) found! 🎉

💾 <b>File Size:</b> <b>{size} MB</b>
📂 <b>Category:</b> {cat}

👉 <b>Would you like to download this app now?</b>"""
            keyboard = {
                'inline_keyboard': [
                    [
                        {'text': '✅ Yes, Download Now', 'callback_data': f'dl_yes_{session_id}'},
                        {'text': '❌ No, Cancel', 'callback_data': f'dl_no_{session_id}'}
                    ]
                ]
            }
            send_msg(chat_id, confirm_msg, keyboard)
            return

        # 2. Search website repository if not in catalog (BOTH LiteAPKs & AndroForever)
        meta = None
        best_cand = None
        cand_source = 'liteapks'

        try:
            lite_candidates = agent.search_and_find_app(query_text)
            if lite_candidates:
                for cand in lite_candidates[:4]:
                    try:
                        m = agent.extract_app_metadata(cand['url'])
                        if m and m.get('download_page'):
                            meta = m
                            best_cand = cand
                            cand_source = 'liteapks'
                            break
                    except Exception:
                        continue
        except Exception as le:
            print(f"[LiteAPKs Search Warning]: {le}")

        if not meta:
            try:
                andro_candidates = androforever_scraper.search_androforever(query_text)
                if andro_candidates:
                    for cand in andro_candidates[:4]:
                        try:
                            m = androforever_scraper.extract_androforever_metadata(cand['url'])
                            if m and m.get('download_page'):
                                meta = m
                                best_cand = cand
                                cand_source = 'androforever'
                                break
                        except Exception:
                            continue
            except Exception as ae:
                print(f"[AndroForever Search Warning]: {ae}")

        if not meta or not best_cand:
            active_scrapes.pop(user_id, None)
            not_found_msg = f"""❌ <b>'{html.escape(query_text)}' is not modded yet!</b>

🛠️ <i>Our developers are actively working on it. As soon as it is ready, tested, and verified, we will post it on {CHANNEL_GETMOD_HANDLE}!</i>

👉 <i>Stay tuned to our official channels for the release announcement!</i>"""
            keyboard = {
                'inline_keyboard': [
                    [{'text': '🔥 Loot Deals at ₹1 😱', 'url': f'https://t.me/{CHANNEL_DEALS_HANDLE.lstrip("@")}'}],
                    [{'text': f'📢 Join Channel {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'}]
                ]
            }
            send_msg(chat_id, not_found_msg, keyboard)
            return

        app_url = best_cand['url']

        # Banned app check
        if agent.is_banned_app(meta.get('title', ''), app_url):
            active_scrapes.pop(user_id, None)
            send_msg(chat_id, f"🚫 <b>'{html.escape(meta.get('title', ''))}'</b> is restricted due to safety policies.")
            return

        # Check if scraped candidate is already in our DB by package or canonical slug
        c_slug = agent.get_canonical_slug(app_url)
        pkg_name = meta.get('package_name', '')
        norm_title = agent.normalize_app_title(meta.get('title', ''))
        cand_ver = agent.clean_app_version(meta.get('version', ''))

        existing_in_db = None
        catalog = agent.load_apps_catalog()
        if c_slug in catalog and str(catalog[c_slug].get('devuploads_url', '')).startswith("https://devuploads.com/"):
            existing_in_db = catalog[c_slug]
        elif pkg_name:
            for s, entry in catalog.items():
                if entry.get('package_name') == pkg_name and str(entry.get('devuploads_url', '')).startswith("https://devuploads.com/"):
                    existing_in_db = entry
                    break
        if not existing_in_db and norm_title:
            for s, entry in catalog.items():
                if agent.normalize_app_title(entry.get('title', '')) == norm_title and str(entry.get('devuploads_url', '')).startswith("https://devuploads.com/"):
                    existing_in_db = entry
                    break

        if existing_in_db:
            prev_ver = agent.clean_app_version(existing_in_db.get('version', ''))
            if not cand_ver or (prev_ver and agent.compare_app_versions(cand_ver, prev_ver) <= 0):
                session_id = secrets.token_hex(6)
                with confirmations_lock:
                    pending_confirmations[session_id] = {
                        'source': 'DATABASE',
                        'chat_id': chat_id,
                        'user_id': user_id,
                        'stage': stage,
                        'user_name': user_name,
                        'app_data': existing_in_db,
                        'query': query_text,
                        'created_at': time.time(),
                        'processing': False
                    }

                title = html.escape(existing_in_db.get('title', query_text))
                ver = html.escape(str(existing_in_db.get('version', 'Latest')))
                cat = html.escape(existing_in_db.get('category', 'Android Mod'))
                size = existing_in_db.get('size_mb', 'N/A')

                confirm_msg = f"""✅ <b>{title}</b> (v{ver}) found! 🎉

💾 <b>File Size:</b> <b>{size} MB</b>
📂 <b>Category:</b> {cat}

👉 <b>Would you like to download this app now?</b>"""
                keyboard = {
                    'inline_keyboard': [
                        [
                            {'text': '✅ Yes, Download Now', 'callback_data': f'dl_yes_{session_id}'},
                            {'text': '❌ No, Cancel', 'callback_data': f'dl_no_{session_id}'}
                        ]
                    ]
                }
                send_msg(chat_id, confirm_msg, keyboard)
                return

        # Resolve APK download page stream info (Zero file download to disk yet)
        if cand_source == 'androforever':
            apk_info = androforever_scraper.resolve_androforever_apk_url(
                meta['download_page'],
                meta.get('title', 'App'),
                meta.get('version', 'Latest'),
                max_size_mb=MAX_UPLOAD_SIZE_MB
            )
        else:
            apk_info = agent.resolve_direct_apk_url(meta['download_page'])
        if not apk_info:
            active_scrapes.pop(user_id, None)
            not_found_msg = f"""❌ <b>'{html.escape(query_text)}' is not modded yet!</b>

🛠️ <i>Our developers are actively working on it. As soon as it is ready, tested, and verified, we will post it on {CHANNEL_GETMOD_HANDLE}!</i>"""
            keyboard = {
                'inline_keyboard': [
                    [{'text': '🔥 Loot Deals at ₹1 😱', 'url': f'https://t.me/{CHANNEL_DEALS_HANDLE.lstrip("@")}'}],
                    [{'text': f'📢 Join Channel {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'}]
                ]
            }
            send_msg(chat_id, not_found_msg, keyboard)
            return

        # Scraped app found on website! Store session for user confirmation
        session_id = secrets.token_hex(6)
        with confirmations_lock:
            pending_confirmations[session_id] = {
                'source': 'SCRAPE',
                'chat_id': chat_id,
                'user_id': user_id,
                'stage': stage,
                'user_name': user_name,
                'meta': meta,
                'apk_info': apk_info,
                'app_url': app_url,
                'cand_source': cand_source,
                'query': query_text,
                'created_at': time.time(),
                'processing': False
            }

        title = html.escape(meta.get('title', query_text))
        ver = html.escape(str(meta.get('version', 'Latest')))
        cat = html.escape(meta.get('category', 'Android Mod'))
        size = apk_info.get('size_mb', 'N/A')

        confirm_msg = f"""✅ <b>{title}</b> (v{ver}) found! 🎉

💾 <b>File Size:</b> <b>{size} MB</b>
📂 <b>Category:</b> {cat}

👉 <b>Would you like to download this app now?</b>"""
        keyboard = {
            'inline_keyboard': [
                [
                    {'text': '✅ Yes, Download Now', 'callback_data': f'dl_yes_{session_id}'},
                    {'text': '❌ No, Cancel', 'callback_data': f'dl_no_{session_id}'}
                ]
            ]
        }
        send_msg(chat_id, confirm_msg, keyboard)

    except Exception as e:
        active_scrapes.pop(user_id, None)
        import traceback
        tb = traceback.format_exc()
        print(f"[Find Worker Error]: {e}")
        print(tb)
        send_msg(chat_id, f"⚠️ An error occurred while searching for your app: {html.escape(str(e))}")


def handle_download_cancel(cb, session_id):
    cb_id = cb.get('id')
    chat_id = cb.get('message', {}).get('chat', {}).get('id')
    msg_id = cb.get('message', {}).get('message_id')
    from_user = cb.get('from', {})
    user_id = from_user.get('id')

    with confirmations_lock:
        session = pending_confirmations.pop(session_id, None)

    active_scrapes.pop(user_id, None)
    answer_callback(cb_id, "❌ Cancelled!")

    app_title = "App"
    if session:
        if session.get('source') == 'DATABASE':
            app_title = session.get('app_data', {}).get('title', 'App')
        elif session.get('meta'):
            app_title = session.get('meta', {}).get('title', 'App')

    cancel_text = f"""❌ <b>Request Cancelled</b>

You have cancelled the download request for <b>'{html.escape(app_title)}'</b>. No files will be processed or uploaded to the server.

👉 <i>You can request any other Android app or game anytime by simply sending its name!</i>"""
    if chat_id and msg_id:
        edit_msg(chat_id, msg_id, cancel_text)
    elif chat_id:
        send_msg(chat_id, cancel_text)


def handle_download_confirm(cb, session_id):
    cb_id = cb.get('id')
    chat_id = cb.get('message', {}).get('chat', {}).get('id')
    msg_id = cb.get('message', {}).get('message_id')
    from_user = cb.get('from', {})
    user_id = from_user.get('id')

    with confirmations_lock:
        session = pending_confirmations.get(session_id)
        if not session:
            answer_callback(cb_id, "⚠️ Session expired. Please search again.", show_alert=True)
            if chat_id and msg_id:
                edit_msg(chat_id, msg_id, "⚠️ <i>This session has expired. Please type and send the app name again.</i>")
            return
        if session.get('processing'):
            answer_callback(cb_id, "⏳ Already processing, please wait...")
            return
        session['processing'] = True

    answer_callback(cb_id, "🚀 Processing your app...")

    source = session.get('source')
    stage = session.get('stage')

    if source == 'DATABASE':
        app_data = session['app_data']
        with confirmations_lock:
            pending_confirmations.pop(session_id, None)
        active_scrapes.pop(user_id, None)
        if chat_id and msg_id:
            edit_msg(chat_id, msg_id, f"⚡ <b>Loading {html.escape(app_data.get('title', 'App'))}...</b>")
        deliver_catalog_app(chat_id, user_id, app_data.get('slug', ''), app_data, stage)
        return

    # SOURCE == 'SCRAPE': Execute download & upload with dynamic live countdown!
    threading.Thread(
        target=execute_scrape_and_deliver_with_countdown,
        args=(session_id, chat_id, msg_id, user_id, session),
        daemon=True
    ).start()


def execute_scrape_and_deliver_with_countdown(session_id, chat_id, msg_id, user_id, session):
    stop_event = threading.Event()
    meta = session['meta']
    apk_info = session['apk_info']
    app_url = session['app_url']
    user_name = session.get('user_name', 'Valued Member')
    stage = session.get('stage')
    query_text = session.get('query', meta.get('title', ''))

    size_mb = apk_info.get('size_mb', 0)
    est_seconds = max(20, min(75, int(size_mb * 0.5) + 15))

    # Edit confirmation message to initial progress & countdown state
    # STRICT WHITE-LABEL: "Downloading and uploading to cloud server..." (NO DevUploads mention)
    init_progress = f"""📥 <b>{html.escape(meta['title'])}</b> found! (v{meta['version']}, {size_mb} MB)

☁️ <i>Downloading and uploading to cloud server...</i>

⏱️ <b>Just {est_seconds} seconds remaining...</b> Your app is getting ready!
⏳ <i>Please hold on a moment...</i>"""
    if chat_id and msg_id:
        edit_msg(chat_id, msg_id, init_progress)

    # Countdown ticker thread updating every 5.0 seconds
    def countdown_ticker():
        start_t = time.time()
        steps = [
            "📦 Downloading APK packages...",
            "☁️ Uploading to cloud server...",
            "⚡ Verifying secure installation...",
            "🚀 Generating high-speed download link..."
        ]
        while not stop_event.is_set():
            if stop_event.wait(5.0):
                break
            elapsed = int(time.time() - start_t)
            rem = max(5, est_seconds - elapsed)

            ratio = min(0.95, elapsed / max(1, est_seconds))
            pct = int(ratio * 100)
            filled = int(pct / 10)
            bar = "█" * filled + "░" * (10 - filled)

            if ratio < 0.35:
                curr_step = steps[0]
            elif ratio < 0.70:
                curr_step = steps[1]
            elif ratio < 0.90:
                curr_step = steps[2]
            else:
                curr_step = steps[3]

            progress_text = f"""📥 <b>{html.escape(meta['title'])}</b> found! (v{meta['version']}, {size_mb} MB)

☁️ <i>Downloading and uploading to cloud server...</i>

⏱️ <b>Just {rem} seconds remaining...</b> Your app is getting ready!
📊 <code>[{bar}] {pct}%</code> • <i>{curr_step}</i>"""
            if chat_id and msg_id:
                try:
                    edit_msg(chat_id, msg_id, progress_text)
                except Exception:
                    pass

    ticker_thread = threading.Thread(target=countdown_ticker, daemon=True)
    ticker_thread.start()

    try:
        # Upload to DevUploads & Post to AndroModApps Channel with user attribution
        dev_url = None
        if size_mb <= MAX_UPLOAD_SIZE_MB:
            dev_url = agent.post_apk_to_telegram(
                meta,
                apk_info,
                target_chat=CHANNEL_GETMOD,
                demanded_by=user_name
            )

        stop_event.set()
        ticker_thread.join(timeout=1.0)

        # STRICT DEVUPLOADS ENFORCEMENT: Never ever output or store a direct LiteAPKs download link!
        if not dev_url or not str(dev_url).strip().startswith("https://devuploads.com/"):
            print(f"[Upload Failure] DevUploads upload failed for '{meta.get('title')}'. Strict policy: NO direct LiteAPKs link allowed.")
            fail_msg = f"""⚠️ <b>Cloud Server Busy for '{html.escape(meta.get('title', query_text))}'!</b>

Our agent downloaded the APK, but the cloud upload server is currently busy or timed out.

👉 <i>Please try requesting '{html.escape(query_text)}' again in 1 minute, and our bot will upload it with a verified high-speed link!</i>"""
            keyboard = {
                'inline_keyboard': [
                    [{'text': '🔄 Retry Request', 'callback_data': f'verify_app_{stage}'}],
                    [{'text': f'📢 Join Channel {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'}]
                ]
            }
            if chat_id and msg_id:
                edit_msg(chat_id, msg_id, fail_msg, keyboard)
            else:
                send_msg(chat_id, fail_msg, keyboard)
            return

        # Update progress card to ready
        if chat_id and msg_id:
            try:
                edit_msg(chat_id, msg_id, f"✅ <b>{html.escape(meta['title'])}</b> is ready! Download link sent below 👇")
            except Exception:
                pass

        app_data = {
            'title': meta.get('title', query_text),
            'slug': agent.get_canonical_slug(app_url),
            'version': meta.get('version', 'Latest'),
            'package_name': meta.get('package_name', ''),
            'devuploads_url': dev_url,
            'category': meta.get('category', 'Android Mod'),
            'size_mb': apk_info.get('size_mb', 0),
            'image_url': meta.get('image_url', ''),
            'last_updated': time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # 5. PERMANENTLY RECORD in sent_apks.json and apps_catalog.json (Zero Duplication in 10 Collection)
        try:
            sent_set = agent.load_sent_history()
            c_slug = agent.get_canonical_slug(app_url)
            norm_title = agent.normalize_app_title(meta.get('title', ''))
            pkg_name = meta.get('package_name', '')
            ver = meta.get('version', '')
            agent.record_posted_apk(sent_set, app_url, c_slug, norm_title, pkg_name, ver)
            agent.update_catalog_entry(meta, apk_info, dev_url)
            print(f"[Catalog & Sent DB Updated]: '{app_data['title']}' (v{ver}) saved with DevUploads link: {dev_url}")
        except Exception as se:
            print(f"[Record Sent Error]: {se}")

        # 6. Deliver to the requesting user in Bot chat with verified download link
        caption, keyboard = build_app_delivery_card(app_data, is_user_request=True)
        send_photo_card(chat_id, app_data.get('image_url'), caption, keyboard)
        record_app_delivered(user_id, stage)
        print(f"[Delivered to User]: '{app_data.get('title')}' successfully sent with DevUploads link to user {user_id} in private chat!")

    except Exception as e:
        stop_event.set()
        import traceback
        tb = traceback.format_exc()
        print(f"[Scrape Worker Error]: {e}")
        print(tb)
        send_msg(chat_id, f"⚠️ An error occurred while processing your request: {html.escape(str(e))}")
    finally:
        with confirmations_lock:
            pending_confirmations.pop(session_id, None)
        active_scrapes.pop(user_id, None)
        agent.cleanup_storage()


def handle_app_search_request(chat_id, user_id, query_text, user_name='Valued Member'):
    query_clean = query_text.strip()
    if not query_clean or query_clean.startswith('/'):
        return

    # Check Eligibility (Day 1 vs Day 2+)
    status, stage, channel_info = verify_user_request_eligibility(user_id)

    if status == 'LIMIT_REACHED':
        send_msg(chat_id, channel_info) # channel_info holds limit text in this case
        return

    if status == 'NEED_CHANNEL':
        ch_name = channel_info['name']
        ch_handle = channel_info['channel']
        ch_url = channel_info['url']

        # Save pending query
        with db_lock:
            users = load_users()
            uid = str(user_id)
            if uid in users:
                users[uid]['pending_query'] = query_clean
                users[uid]['user_name'] = user_name
                save_users(users)

        lock_msg = f"""🔒 <b>Channel Subscription Required!</b>

📱 You requested to download: <b>'{html.escape(query_clean)}'</b>

⚠️ To unlock your download link, please join our partner channel <b>{ch_name} ({ch_handle})</b> first!

After joining, tap the <b>'🔄 Verify & Get App'</b> button below:"""
        keyboard = {
            'inline_keyboard': [
                [{'text': f'📢 Join {ch_name}', 'url': ch_url}],
                [{'text': '🔄 Verify & Get App', 'callback_data': f'verify_app_{stage}'}]
            ]
        }
        send_msg(chat_id, lock_msg, keyboard)
        return

    # User is ALLOWED!
    if user_id in active_scrapes:
        start_ts = active_scrapes.get(user_id, 0)
        if time.time() - start_ts < 120:
            send_msg(chat_id, """⏳ <b>Your previous request is still in progress!</b>

Please wait a moment before sending another query.""")
            return

    active_scrapes[user_id] = time.time()
    t = threading.Thread(
        target=find_app_and_request_confirmation_worker,
        args=(chat_id, user_id, query_clean, stage, user_name),
        daemon=True
    )
    t.start()

def handle_callback_query(cb):
    cb_id = cb.get('id')
    from_user = cb.get('from', {})
    user_id = from_user.get('id')
    chat_id = cb.get('message', {}).get('chat', {}).get('id')
    data = cb.get('data', '')

    if data.startswith('dl_yes_'):
        session_id = data[len('dl_yes_'):]
        handle_download_confirm(cb, session_id)
        return

    elif data.startswith('dl_no_'):
        session_id = data[len('dl_no_'):]
        handle_download_cancel(cb, session_id)
        return

    if data.startswith('verify_file_'):
        file_code = data[len('verify_file_'):]
        if is_member_of_channel(CHANNEL_GETMOD, user_id):
            answer_callback(cb_id, "✅ Verified! Delivering your file now...", show_alert=False)
            handle_shared_file_delivery(chat_id, user_id, file_code)
        else:
            answer_callback(cb_id, f"⚠️ You haven't joined {CHANNEL_GETMOD_HANDLE} yet! Please join the channel first.", show_alert=True)

    elif data.startswith('verify_app_'):
        stage = data[len('verify_app_'):]
        target_ch = CHANNEL_DEALS if 'app2' in stage or 'daily_app1' in stage else CHANNEL_GETMOD
        target_name = CHANNEL_DEALS_HANDLE if 'app2' in stage or 'daily_app1' in stage else CHANNEL_GETMOD_HANDLE

        if is_member_of_channel(target_ch, user_id):
            answer_callback(cb_id, "✅ Channel verified! Preparing your app...", show_alert=False)
            # Retrieve pending query
            with db_lock:
                users = load_users()
                pending_q = users.get(str(user_id), {}).get('pending_query', '')
            if pending_q:
                first_name = from_user.get('first_name', '').strip()
                last_name = from_user.get('last_name', '').strip()
                u_name = f"{first_name} {last_name}".strip() or first_name or "Community Member"
                handle_app_search_request(chat_id, user_id, pending_q, user_name=u_name)
            else:
                send_msg(chat_id, "✅ <b>Channel verified!</b>\n\nType and send the name of any app you would like to download:")
        else:
            answer_callback(cb_id, f"⚠️ You haven't joined {target_name} yet! Please join the channel first.", show_alert=True)

    elif data == 'check_stats':
        handle_stats(chat_id)
        answer_callback(cb_id)

# ==============================================================================
# 🚀 /START & COMMAND HANDLERS
# ==============================================================================
def handle_start(chat_id, user_info, args=""):
    name = user_info.get('first_name', 'Friend')
    user_id = user_info.get('id')
    get_user_state(user_id) # initializes user profile

    # Check if started via shared file deep-link (/start file_xyz)
    if args.startswith('file_'):
        file_code = args[len('file_'):]
        handle_shared_file_delivery(chat_id, user_id, file_code)
        return

    welcome_text = (
        f"👋 <b>Hello, {html.escape(name)}! Welcome to File Sharing & App Wishlist!</b> ☁️⚡\n\n"
        f"This bot provides <b>2 powerful automated tools</b> in one place:\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>1. APP WISHLIST & MOD STORE:</b>\n"
        f"• Send the name of any Android app or game (e.g., <code>Spotify</code>, <code>CapCut</code>, <code>KineMaster</code>).\n"
        f"• The bot instantly fetches its <b>100% verified MOD APK + high-speed download link</b> for you!\n"
        f"• If it's not yet in our database, our autonomous agent instantly builds, tests, uploads, and delivers it directly to you!\n\n"
        f"📁 <b>2. 2,000 MB (2 GB) CLOUD FILE SHARING:</b>\n"
        f"• Send any file, video, APK, document, photo, or audio file up to 2GB.\n"
        f"• Receive an instant <b>permanent download link</b> to share with anyone worldwide!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 <b>RULES & DAILY QUOTA:</b>\n"
        f"• <b>Day 1:</b> 1st App 100% Free! 2nd App requires {CHANNEL_DEALS_HANDLE}, 3rd App requires {CHANNEL_GETMOD_HANDLE}.\n"
        f"• <b>Day 2 Onwards:</b> <b>2 Free Apps every day</b> (stay subscribed to both channels to keep access active)!\n\n"
        f"👇 <i>Type any app name below or send a file to get started right away!</i>"
    )
    keyboard = {
        'inline_keyboard': [
            [
                {'text': f'📢 Join {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'},
                {'text': f'🔥 Join {CHANNEL_DEALS_HANDLE}', 'url': f'https://t.me/{CHANNEL_DEALS_HANDLE.lstrip("@")}'}
            ],
            [{'text': '📊 My Quota & Stats', 'callback_data': 'check_stats'}]
        ]
    }
    send_msg(chat_id, welcome_text, keyboard)

def handle_stats(chat_id):
    users = load_users()
    files = load_files()
    catalog = agent.load_apps_catalog()
    total_users = len(users)
    total_files = len(files)
    total_catalog = len(catalog)

    uid = str(chat_id)
    user_data = users.get(uid, {})
    d_apps = user_data.get('daily_apps', 0)
    life_apps = user_data.get('lifetime_apps', 0)
    is_d1 = not user_data.get('day_1_completed')
    quota_max = 3 if is_d1 else 2

    stats_msg = (
        f"📊 <b>File Sharing & App Wishlist — Live Stats:</b>\n\n"
        f"👤 <b>Your Account:</b>\n"
        f"• <b>Status:</b> {'🌟 Day 1 (Welcome Mode)' if is_d1 else '🚀 Regular Daily Mode'}\n"
        f"• <b>Today Used:</b> <b>{d_apps} / {quota_max} Apps</b>\n"
        f"• <b>Lifetime Apps Received:</b> {life_apps}\n\n"
        f"🌐 <b>Global Bot Stats:</b>\n"
        f"• <b>Total Registered Users:</b> {total_users}\n"
        f"• <b>Cached Modded Apps:</b> {total_catalog}\n"
        f"• <b>Cloud Files Hosted:</b> {total_files}\n\n"
        f"⚡ <i>Enjoy unlimited high-speed cloud file storage & daily modded apps!</i>"
    )
    send_msg(chat_id, stats_msg)

# ==============================================================================
# 🔄 MAIN TELEGRAM POLLING LOOP
# ==============================================================================
def main_poll_loop():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] @{BOT_USERNAME} is ONLINE & Polling Telegram (100% English Mode)...")
    offset = None

    while True:
        try:
            params = {'timeout': 20}
            if offset:
                params['offset'] = offset

            res = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=25).json()
            if not res.get('ok'):
                time.sleep(2)
                continue

            updates = res.get('result', [])
            for u in updates:
                offset = u['update_id'] + 1

                # 1. Callback Query Handler
                if 'callback_query' in u:
                    handle_callback_query(u['callback_query'])
                    continue

                msg = u.get('message')
                if not msg:
                    continue

                chat_id = msg.get('chat', {}).get('id')
                from_user = msg.get('from', {})
                user_id = from_user.get('id')
                text = msg.get('text', '').strip()

                # 2. File Upload Handler
                if any(k in msg for k in ['document', 'video', 'audio', 'photo', 'voice']):
                    handle_incoming_file(msg, from_user)
                    continue

                # 3. Commands & Text Messages
                if text.startswith('/start'):
                    parts = text.split(maxsplit=1)
                    args = parts[1].strip() if len(parts) > 1 else ""
                    handle_start(chat_id, from_user, args)

                elif text in ['📊 My Daily Quota', '/stats']:
                    handle_stats(chat_id)

                elif text in ['📢 Official Channels', '/channels']:
                    ch_msg = (
                        f"📢 <b>Our Official Telegram Channels:</b>\n\n"
                        f"1. <b>GETMOD Official</b>: {CHANNEL_GETMOD_HANDLE}\n"
                        f"   👉 <i>Daily Verified Modded Apps & PC Software</i>\n\n"
                        f"2. <b>Deals Fetcher</b>: {CHANNEL_DEALS_HANDLE}\n"
                        f"   👉 <i>Best Loot Deals, Discount Coupons & Price Drops</i>\n\n"
                        f"⚡ <i>Stay subscribed to both channels to unlock your daily on-demand app requests!</i>"
                    )
                    kb = {
                        'inline_keyboard': [
                            [{'text': f'📢 Join {CHANNEL_GETMOD_HANDLE}', 'url': f'https://t.me/{CHANNEL_GETMOD_HANDLE.lstrip("@")}'}],
                            [{'text': f'🔥 Join {CHANNEL_DEALS_HANDLE}', 'url': f'https://t.me/{CHANNEL_DEALS_HANDLE.lstrip("@")}'}]
                        ]
                    }
                    send_msg(chat_id, ch_msg, kb)

                elif text in ['🔍 Search Modded App', '📁 Share a File']:
                    if text == '🔍 Search Modded App':
                        send_msg(chat_id, "👇 <b>Type and send the name of any Android app or game you want:</b>\n\n(e.g., <code>Spotify</code>, <code>CapCut</code>, <code>Lightroom</code>)")
                    else:
                        send_msg(chat_id, "📁 <b>Send any file (up to 2,000 MB / 2 GB) here!</b>\n\nThe bot will instantly generate a permanent high-speed cloud sharing link for you.")

                elif text and not text.startswith('/'):
                    # User sent an App Search Query!
                    first_name = from_user.get('first_name', '').strip()
                    last_name = from_user.get('last_name', '').strip()
                    u_name = f"{first_name} {last_name}".strip() or first_name or "Community Member"
                    handle_app_search_request(chat_id, user_id, text, user_name=u_name)

        except Exception as e:
            print(f"[Polling Error]: {e}")
            time.sleep(3)

if __name__ == '__main__':
    main_poll_loop()
