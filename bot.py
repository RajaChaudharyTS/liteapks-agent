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
import os
import sys
import time
import json
import html
import re
import threading
import secrets
import urllib.parse
import requests

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    except Exception:
        pass

# Add current folder to path
sys.path.insert(0, os.path.dirname(__file__))

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    ADMIN_CHAT_ID,
    DATABASE_FILE,
    MAX_APKS_PER_RUN
)
import agent

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Thread-safe pipeline state & timers
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "scheduler_config.json")

# ==============================================================================
# ☁️ ZERO-DISK TELEGRAM CLOUD FILE-STORE ENGINE (Option 1)
# 100% Hosted on Cloud Storage • 0 MB Local Disk • 2 GB Max Size • Force Channel Join
# ==============================================================================
FILE_STORE_PATH = os.path.join(os.path.dirname(__file__), "file_store.json")

def load_file_store():
    if os.path.exists(FILE_STORE_PATH):
        try:
            with open(FILE_STORE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_file_store(store):
    try:
        with open(FILE_STORE_PATH, 'w', encoding='utf-8') as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        print(f"[FileStore Save Error]: {e}")

def is_user_channel_member(user_id):
    if str(user_id) == str(ADMIN_CHAT_ID):
        return True
    try:
        res = send_tg('getChatMember', {'chat_id': CHANNEL_ID, 'user_id': user_id})
        if res.get('ok'):
            status = res.get('result', {}).get('status', '')
            return status in ['creator', 'administrator', 'member', 'restricted']
    except Exception as e:
        print(f"[Membership Check Error]: {e}")
    return False

def store_telegram_file(msg, from_user):
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
        return None

    size_mb = round(file_size / (1024 * 1024), 2)
    file_code = secrets.token_urlsafe(6)

    store = load_file_store()
    store[file_code] = {
        'file_id': file_id,
        'file_type': file_type,
        'file_name': file_name,
        'file_size_mb': size_mb,
        'caption': caption,
        'uploader_id': from_user.get('id'),
        'uploader_name': from_user.get('first_name', ''),
        'uploader_username': from_user.get('username', ''),
        'created_at': time.time(),
        'downloads_count': 0
    }
    save_file_store(store)
    return file_code, store[file_code]

def send_stored_file(chat_id, file_record):
    file_type = file_record.get('file_type')
    file_id = file_record.get('file_id')
    raw_caption = file_record.get('caption', '')
    if raw_caption:
        caption = f"{html.escape(raw_caption)}\n\n⚡ <i>Delivered via @AndroModApps_Upload_Bot</i>"
    else:
        caption = f"📁 <b>{html.escape(file_record.get('file_name', 'File'))}</b> ({file_record.get('file_size_mb', 0)} MB)\n⚡ <i>Delivered via @AndroModApps_Upload_Bot</i>"

    payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
    if file_type == 'document':
        payload['document'] = file_id
        return send_tg('sendDocument', payload)
    elif file_type == 'video':
        payload['video'] = file_id
        return send_tg('sendVideo', payload)
    elif file_type == 'audio':
        payload['audio'] = file_id
        return send_tg('sendAudio', payload)
    elif file_type == 'photo':
        payload['photo'] = file_id
        return send_tg('sendPhoto', payload)
    elif file_type == 'voice':
        payload['voice'] = file_id
        return send_tg('sendVoice', payload)
    else:
        payload['document'] = file_id
        return send_tg('sendDocument', payload)

def handle_public_start(chat_id, user_info=None):
    name = user_info.get('first_name', 'Friend') if user_info else 'Friend'
    welcome_msg = (
        f"👋 <b>Welcome, {html.escape(name)}, to AndroModApps Cloud Storage!</b> ☁️⚡\n\n"
        f"Send me <b>any file, APK, ZIP, PDF, Video, or Audio</b> and I will store it on high-speed cloud storage and generate a <b>permanent shareable link</b> for you!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>KEY FEATURES:</b>\n"
        f"• 📦 <b>File Size:</b> Up to <b>2,000 MB (2 GB)</b> per file!\n"
        f"• ♾️ <b>Limit:</b> 100% Free & Unlimited files forever\n"
        f"• 🚀 <b>High-Speed Cloud CDN:</b> Instant direct download\n"
        f"• 🔗 <b>Share Anywhere:</b> Share link with friends or groups\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ <b>DISCLAIMER:</b>\n"
        f"<i>This bot operates strictly as an automated cloud sharing utility. The bot owner holds NO responsibility for user-uploaded files. The uploading user assumes 100% legal responsibility for their content. No CSAM or malicious files allowed.</i>\n\n"
        f"👉 <b>Just send or forward any file here to get your instant link!</b>"
    )
    reply_markup = {
        'inline_keyboard': [
            [{'text': '📢 Join Official Channel @AndroModApps', 'url': 'https://t.me/AndroModApps'}]
        ]
    }
    send_tg('sendMessage', {
        'chat_id': chat_id,
        'text': welcome_msg,
        'parse_mode': 'HTML',
        'reply_markup': json.dumps(reply_markup)
    })

def handle_file_upload(msg, from_user):
    chat_id = msg.get('chat', {}).get('id')
    stored = store_telegram_file(msg, from_user)
    if not stored:
        return

    file_code, meta = stored
    bot_username = "AndroModApps_Upload_Bot"
    share_link = f"https://t.me/{bot_username}?start=file_{file_code}"

    card_text = (
        f"✅ <b>File Stored in Cloud Successfully!</b> ☁️\n\n"
        f"📁 <b>Name:</b> <code>{html.escape(meta['file_name'])}</code>\n"
        f"💾 <b>Size:</b> <b>{meta['file_size_mb']} MB</b>\n"
        f"🔗 <b>Your Permanent Shareable Link:</b>\n"
        f"👉 <code>{share_link}</code>\n\n"
        f"💡 <i>Share this link with your friends. When they click, they can download this file instantly!</i>"
    )

    encoded_name = urllib.parse.quote(meta['file_name'])
    share_button_url = f"https://t.me/share/url?url={share_link}&text=Download%20{encoded_name}%20via%20AndroModApps%20Cloud"
    keyboard = {
        'inline_keyboard': [
            [{'text': '📤 Share Link with Friends', 'url': share_button_url}],
            [{'text': '📢 Join Channel @AndroModApps', 'url': 'https://t.me/AndroModApps'}]
        ]
    }

    send_tg('sendMessage', {
        'chat_id': chat_id,
        'text': card_text,
        'parse_mode': 'HTML',
        'reply_markup': json.dumps(keyboard)
    })

def handle_file_request(chat_id, user_id, file_code, user_info=None):
    store = load_file_store()
    file_record = store.get(file_code)
    if not file_record:
        send_tg('sendMessage', {
            'chat_id': chat_id,
            'text': "❌ <b>File Not Found!</b>\n\nThis file link is invalid or may have been deleted.",
            'parse_mode': 'HTML'
        })
        return

    # Force Channel Membership Check (@AndroModApps)
    if not is_user_channel_member(user_id):
        fname = html.escape(file_record.get('file_name', 'File'))
        fsize = file_record.get('file_size_mb', 0)
        lock_msg = (
            f"🔒 <b>File Locked! (Membership Required)</b>\n\n"
            f"📁 <b>File:</b> <code>{fname}</code>\n"
            f"💾 <b>Size:</b> <b>{fsize} MB</b>\n\n"
            f"⚠️ <i>To download this file, please join our official channel <b>@AndroModApps</b> first!</i>\n\n"
            f"After joining, click the <b>'🔄 Verify & Get File'</b> button below:"
        )
        keyboard = {
            'inline_keyboard': [
                [{'text': '📢 Join Channel @AndroModApps', 'url': 'https://t.me/AndroModApps'}],
                [{'text': '🔄 Verify & Get File', 'callback_data': f'verify_sub_{file_code}'}]
            ]
        }
        send_tg('sendMessage', {
            'chat_id': chat_id,
            'text': lock_msg,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        })
        return

    # Delivered!
    file_record['downloads_count'] = file_record.get('downloads_count', 0) + 1
    save_file_store(store)

    send_tg('sendMessage', {
        'chat_id': chat_id,
        'text': "🚀 <i>Sending your file from high-speed cloud...</i>",
        'parse_mode': 'HTML'
    })
    send_stored_file(chat_id, file_record)

def handle_sub_verification(cb_id, user_id, file_code, from_user):
    store = load_file_store()
    file_record = store.get(file_code)
    if not file_record:
        answer_callback(cb_id, "❌ File no longer available.", show_alert=True)
        return

    if is_user_channel_member(user_id):
        answer_callback(cb_id, "✅ Verified! Sending file now...", show_alert=False)
        file_record['downloads_count'] = file_record.get('downloads_count', 0) + 1
        save_file_store(store)
        send_stored_file(user_id, file_record)
    else:
        answer_callback(cb_id, "⚠️ You haven't joined @AndroModApps yet! Please join the channel first.", show_alert=True)


def get_interval_minutes():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return max(5, int(data.get('interval_minutes', 60)))
    except Exception:
        pass
    return 60

def set_interval_minutes(mins):
    mins = max(5, int(mins))
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'interval_minutes': mins, 'updated_at': time.time()}, f, indent=2)
    except Exception as e:
        print(f"[Config Save Error]: {e}")
    return mins

def get_interval_display(mins=None):
    if mins is None:
        mins = get_interval_minutes()
    if mins == 60:
        return "1 Hour (60m)"
    elif mins % 60 == 0:
        return f"{mins // 60} Hours ({mins}m)"
    elif mins > 60:
        return f"{mins / 60:.1f} Hours ({mins}m)"
    else:
        return f"{mins} Minutes"

is_pipeline_running = False
pipeline_lock = threading.Lock()
last_run_time = None
next_scheduled_run_time = time.time() + (get_interval_minutes() * 60)
user_states = {}

def send_tg(method, payload=None, files=None):
    try:
        url = f"{API_BASE}/{method}"
        res = requests.post(url, data=payload, files=files, timeout=25)
        data = res.json()
        if not data.get('ok'):
            print(f"[Telegram API Error] {method} response: {data}")
        return data
    except Exception as e:
        print(f"[Telegram API Exception] {method}: {e}")
        return {}

def send_admin_message(text, reply_markup=None):
    if not ADMIN_CHAT_ID:
        return {}
    payload = {
        'chat_id': ADMIN_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    return send_tg('sendMessage', payload)

def edit_admin_message(message_id, text, reply_markup=None):
    if not ADMIN_CHAT_ID or not message_id:
        return {}
    payload = {
        'chat_id': ADMIN_CHAT_ID,
        'message_id': message_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    return send_tg('editMessageText', payload)

def answer_callback(callback_query_id, text=None, show_alert=False):
    payload = {
        'callback_query_id': callback_query_id,
        'show_alert': 'true' if show_alert else 'false'
    }
    if text:
        payload['text'] = text
    return send_tg('answerCallbackQuery', payload)

def get_inline_keyboard():
    cur_mins = get_interval_minutes()
    return {
        'inline_keyboard': [
            [
                {'text': '🚀 Scrape & Post Now (10 APKs)', 'callback_data': 'run_now'},
                {'text': '➕ Post Custom App', 'callback_data': 'custom_app'}
            ],
            [
                {'text': f'⏱️ Timer ({cur_mins}m)', 'callback_data': 'timer'},
                {'text': '⚙️ Set Interval', 'callback_data': 'set_interval'}
            ],
            [
                {'text': '📊 Database Stats', 'callback_data': 'stats'},
                {'text': '🔄 Refresh Dashboard', 'callback_data': 'refresh'}
            ]
        ]
    }

def get_interval_presets_keyboard():
    return {
        'inline_keyboard': [
            [
                {'text': '⏱️ 15 Min', 'callback_data': 'set_int_15'},
                {'text': '⏱️ 30 Min', 'callback_data': 'set_int_30'},
                {'text': '⏱️ 45 Min', 'callback_data': 'set_int_45'}
            ],
            [
                {'text': '⭐ 60 Min (1h)', 'callback_data': 'set_int_60'},
                {'text': '⏱️ 90 Min (1.5h)', 'callback_data': 'set_int_90'},
                {'text': '⏱️ 120 Min (2h)', 'callback_data': 'set_int_120'}
            ],
            [
                {'text': '🔙 Back to Dashboard', 'callback_data': 'refresh'}
            ]
        ]
    }

def get_reply_keyboard():
    """Persistent bottom keyboard on mobile/desktop for 100% reliable 1-tap interaction."""
    return {
        'keyboard': [
            [
                {'text': '🚀 Scrape & Post Now'},
                {'text': '➕ Post Custom App'}
            ],
            [
                {'text': '⏱️ Next Run Countdown'},
                {'text': '⚙️ Set Interval'}
            ],
            [
                {'text': '📊 Database Stats'},
                {'text': '🔄 Refresh Dashboard'}
            ]
        ],
        'resize_keyboard': True,
        'is_persistent': True
    }

def register_bot_commands():
    """Registers standard Telegram / commands menu visible in bottom-left [/] button."""
    commands = [
        {'command': 'start', 'description': 'Open Control Panel & Keyboards'},
        {'command': 'post', 'description': 'Scrape & Post 10 APKs Immediately'},
        {'command': 'postapp', 'description': 'Post Specific App (e.g. /postapp CapCut)'},
        {'command': 'setinterval', 'description': 'Set Auto-Run Interval in Minutes (e.g. /setinterval 60)'},
        {'command': 'timer', 'description': 'Next Auto-Run Countdown Timer'},
        {'command': 'stats', 'description': 'View Database & Agent Stats'},
        {'command': 'preview', 'description': 'Preview Next Priority Apps'},
        {'command': 'help', 'description': 'Bot Guide & Command List'}
    ]
    try:
        url = f"{API_BASE}/setMyCommands"
        r = requests.post(url, json={'commands': commands}, timeout=15)
        print(f"[Telegram] Bot commands registered: {r.status_code}")
    except Exception as e:
        print(f"[Telegram] setMyCommands error: {e}")

def build_dashboard_text(user_info=None):
    if user_info is None:
        user_info = {}
    db_items = agent.load_sent_history()
    status_str = "🟡 Active (Scraping & Posting)" if is_pipeline_running else "🟢 Ready (Idle)"
    last_str = time.strftime('%H:%M:%S IST', time.localtime(last_run_time)) if last_run_time else "Not executed yet"
    name = user_info.get('first_name', 'Admin')

    now = time.time()
    rem = max(0, int(next_scheduled_run_time - now))
    mins = rem // 60
    secs = rem % 60
    sched_str = time.strftime('%H:%M:%S IST', time.localtime(next_scheduled_run_time))
    countdown_display = "🟡 <b>Running Live Now...</b>" if is_pipeline_running else f"⏳ <b>{mins:02d}m {secs:02d}s</b> (at {sched_str})"

    return (
        f"🤖 <b>LiteAPKs MOD Agent — Control Panel</b>\n\n"
        f"👑 <b>Admin:</b> {html.escape(name)} (<code>{ADMIN_CHAT_ID}</code>)\n"
        f"📢 <b>Target Channel:</b> {CHANNEL_ID}\n"
        f"📦 <b>Broadcasted Database:</b> <b>{len(db_items)}</b> APKs\n"
        f"⏱️ <b>Next Auto-Run:</b> {countdown_display}\n"
        f"🔁 <b>Interval:</b> Every <b>{get_interval_display()}</b>\n"
        f"🕒 <b>Last Run:</b> {last_str}\n"
        f"⚡ <b>Engine Status:</b> {status_str}\n\n"
        f"👇 <i>Use the buttons below or bottom keyboard to control the agent:</i>"
    )

def handle_start(chat_id, user_info=None):
    msg = build_dashboard_text(user_info)
    send_tg('sendMessage', {
        'chat_id': chat_id,
        'text': "⌨️ <i>Persistent control keyboard enabled.</i>",
        'parse_mode': 'HTML',
        'reply_markup': json.dumps(get_reply_keyboard())
    })
    send_admin_message(msg, get_inline_keyboard())

def handle_stats(chat_id, message_id=None):
    db_items = agent.load_sent_history()
    status_str = "🟡 Scraping / Uploading" if is_pipeline_running else "🟢 Ready / Idle"
    last_str = time.strftime('%H:%M:%S IST', time.localtime(last_run_time)) if last_run_time else "None yet"
    
    now = time.time()
    rem = max(0, int(next_scheduled_run_time - now))
    mins = rem // 60
    secs = rem % 60
    sched_str = time.strftime('%H:%M:%S IST', time.localtime(next_scheduled_run_time))
    timer_txt = "🟡 In Progress" if is_pipeline_running else f"⏳ {mins:02d}m {secs:02d}s (at {sched_str})"

    msg = (
        f"📊 <b>LiteAPKs MOD Agent Statistics:</b>\n\n"
        f"• <b>Total Database:</b> <b>{len(db_items)}</b> APKs recorded\n"
        f"• <b>Target Channel:</b> {CHANNEL_ID}\n"
        f"• <b>Batch Limit:</b> {MAX_APKS_PER_RUN} APKs per run\n"
        f"• <b>Engine Status:</b> {status_str}\n"
        f"• <b>Next Run Timer:</b> {timer_txt}\n"
        f"• <b>Last Run:</b> {last_str}\n"
        f"• <b>Auto-Scheduler:</b> 🟢 Active (Every {get_interval_display()})\n"
        f"• <b>Timestamp:</b> {time.strftime('%Y-%m-%d %H:%M:%S IST')}"
    )
    if message_id:
        edit_admin_message(message_id, msg, get_inline_keyboard())
    else:
        send_admin_message(msg, get_inline_keyboard())

def handle_timer(chat_id, message_id=None):
    now = time.time()
    if is_pipeline_running:
        msg = (
            f"⏱️ <b>MOD Agent Execution Status:</b>\n\n"
            f"🟡 <b>Pipeline is currently RUNNING!</b>\n"
            f"Live process updates are streaming in chat.\n\n"
            f"📢 <b>Target Channel:</b> {CHANNEL_ID}"
        )
    else:
        rem = max(0, int(next_scheduled_run_time - now))
        mins = rem // 60
        secs = rem % 60
        sched_time = time.strftime('%H:%M:%S IST', time.localtime(next_scheduled_run_time))
        msg = (
            f"⏱️ <b>Next Auto-Run Countdown:</b>\n\n"
            f"⏳ <b>Time Remaining:</b> <b>{mins:02d}m {secs:02d}s</b>\n"
            f"🕒 <b>Scheduled At:</b> <code>{sched_time}</code>\n"
            f"🔁 <b>Interval:</b> Every {get_interval_display()}\n"
            f"📢 <b>Channel:</b> {CHANNEL_ID}\n\n"
            f"💡 <i>You can tap '🚀 Scrape & Post Now' anytime to run immediately!</i>"
        )
    if message_id:
        edit_admin_message(message_id, msg, get_inline_keyboard())
    else:
        send_admin_message(msg, get_inline_keyboard())

def handle_preview(chat_id):
    now = time.time()
    rem = max(0, int(next_scheduled_run_time - now))
    mins = rem // 60
    secs = rem % 60
    sched_str = time.strftime('%H:%M:%S IST', time.localtime(next_scheduled_run_time))
    msg = (
        f"🎯 <b>On-Time Live Scraping Policy Active:</b>\n\n"
        f"• <b>Next Live Scrape:</b> <code>{sched_str}</code> (in {mins:02d}m {secs:02d}s)\n"
        f"• <b>Scraping Rule:</b> No apps are pre-scraped. As soon as the timer reaches 0, live scraping starts on-the-spot.\n"
        f"• <b>List Timing:</b> Fresh list of 10 apps appears in chat right after scraping completes, followed by auto-uploads.\n\n"
        f"💡 <i>(If you wish to run immediately, click <b>'🚀 Scrape & Post Now'</b>)</i>"
    )
    send_admin_message(msg, get_inline_keyboard())

def handle_custom_app_prompt(chat_id):
    user_states[str(chat_id)] = 'awaiting_custom_app'
    prompt_txt = (
        f"✏️ <b>Manual App Post Mode:</b>\n\n"
        f"Send the <b>App Name</b> (and optional version) to search and publish:\n\n"
        f"💡 <b>Examples:</b>\n"
        f"• <code>CapCut</code> ➔ Searches & posts the latest version on LiteAPKs\n"
        f"• <code>Spotify 8.9.18</code> ➔ Posts specific version 8.9.18\n"
        f"• <code>KineMaster</code>\n"
        f"• <code>Subway Surfers</code>\n\n"
        f"👇 <i>Type the app name below and hit send:</i>"
    )
    send_admin_message(prompt_txt)

def handle_set_interval_prompt(chat_id, message_id=None):
    user_states[str(chat_id)] = 'awaiting_interval'
    current_mins = get_interval_minutes()
    display_str = get_interval_display(current_mins)
    
    prompt_txt = (
        f"⚙️ <b>Set Auto-Run Interval / Timer:</b>\n\n"
        f"• <b>Current Interval:</b> Every <b>{display_str}</b>\n\n"
        f"👇 <b>Choose a quick preset below, or reply with ANY custom number in minutes:</b>\n"
        f"• <code>60</code> for 1 hour\n"
        f"• <code>45</code> for 45 minutes\n"
        f"• <code>30</code> for 30 minutes\n"
        f"• <code>90</code> for 1.5 hours\n"
        f"• <code>120</code> for 2 hours\n\n"
        f"<i>(Range: 5 to 1440 minutes / 24 hours)</i>"
    )
    if message_id:
        edit_admin_message(message_id, prompt_txt, get_interval_presets_keyboard())
    else:
        send_admin_message(prompt_txt, get_interval_presets_keyboard())

def apply_new_interval(val, chat_id, message_id=None):
    global next_scheduled_run_time
    try:
        clean = str(val).strip()
        for prefix in ['/setinterval', '/interval', '/settimer']:
            if clean.lower().startswith(prefix):
                clean = clean[len(prefix):].strip()
        mins = int(clean)
        if mins < 5:
            send_admin_message("⚠️ <b>Minimum interval is 5 minutes</b> to prevent server rate-limiting. Please enter a value between 5 and 1440 minutes.", get_inline_keyboard())
            return
        if mins > 1440:
            send_admin_message("⚠️ <b>Maximum interval is 1440 minutes (24 hours)</b>. Please enter a value between 5 and 1440 minutes.", get_inline_keyboard())
            return

        set_interval_minutes(mins)
        next_scheduled_run_time = time.time() + (mins * 60)
        sched_str = time.strftime('%H:%M:%S IST', time.localtime(next_scheduled_run_time))
        disp = get_interval_display(mins)

        success_msg = (
            f"✅ <b>Auto-Run Schedule Updated Successfully!</b>\n\n"
            f"• <b>New Interval:</b> Every <b>{disp}</b>\n"
            f"• <b>Next Run Scheduled At:</b> <code>{sched_str}</code>\n"
            f"• <b>Status:</b> 🟢 Active & Persistent\n\n"
            f"<i>The MOD Agent will now automatically scrape and publish 10 APKs every {disp}.</i>"
        )
        if message_id:
            edit_admin_message(message_id, success_msg, get_inline_keyboard())
        else:
            send_admin_message(success_msg, get_inline_keyboard())
    except ValueError:
        send_admin_message("⚠️ <b>Invalid input!</b> Please enter a valid number of minutes (e.g. <code>60</code>, <code>45</code>, <code>30</code>, <code>90</code>, <code>120</code>).", get_inline_keyboard())

def process_custom_app_worker(query_text, chat_id):
    """Processes an on-demand custom app requested by the admin."""
    parts = query_text.strip().split()
    app_name = query_text.strip()
    target_version = None

    if len(parts) >= 2:
        last_part = parts[-1]
        if re.match(r'^[vV]?\d+(\.\d+)+.*$', last_part):
            target_version = last_part.lstrip('vV')
            app_name = " ".join(parts[:-1])

    send_admin_message(
        f"🔍 <b>Manual App Request Received:</b>\n\n"
        f"📱 <b>App:</b> <code>{html.escape(app_name)}</code>\n"
        f"🏷️ <b>Version:</b> <code>{target_version if target_version else 'Latest Available'}</code>\n\n"
        f"<i>Searching LiteAPKs and preparing DevUploads pipeline...</i>"
    )

    def on_custom_status(event, data):
        if event == 'found':
            send_admin_message(
                f"🎯 <b>App Found on LiteAPKs:</b>\n"
                f"📱 <b>{html.escape(data.get('title', ''))}</b>\n"
                f"🔗 <code>{data.get('url', '')}</code>\n"
                f"⏳ <i>Resolving direct APK download stream...</i>"
            )
        elif event == 'downloading':
            send_admin_message(
                f"📥 <b>Downloading APK to Server:</b>\n"
                f"📱 <b>{html.escape(data.get('title', ''))}</b> (v{data.get('version', '')})\n"
                f"💾 Size: <b>{data.get('size_mb', 0)} MB</b>"
            )
        elif event == 'app_uploading':
            send_admin_message(
                f"☁️ <b>Uploading to DevUploads:</b>\n"
                f"📱 <b>{html.escape(data.get('title', ''))}</b> (v{data.get('version', '')})\n"
                f"🔄 <i>Attempting high-speed multi-part streaming (up to 4 retries)...</i>"
            )
        elif event == 'app_link_ready':
            send_admin_message(
                f"✅ <b>DevUploads Upload Success!</b>\n"
                f"🔗 <code>{data.get('devuploads_url', '')}</code>\n"
                f"🗑️ <i>Local file removed. Publishing rich card to {CHANNEL_ID}...</i>"
            )

    try:
        res = agent.post_single_custom_app(
            app_name=app_name,
            target_version=target_version,
            status_callback=on_custom_status,
            target_chat=CHANNEL_ID
        )

        if res.get('success'):
            send_admin_message(
                f"🎉 <b>Manual App Successfully Published!</b>\n\n"
                f"📱 <b>App:</b> {html.escape(res.get('title', ''))}\n"
                f"🏷️ <b>Version:</b> {html.escape(str(res.get('version', '')))}\n"
                f"💾 <b>Size:</b> {res.get('size_mb', 0)} MB\n"
                f"🔗 <b>DevUploads:</b> <a href='{res.get('devuploads_url', '')}'>{res.get('devuploads_url', '')}</a>\n"
                f"📢 <b>Published to:</b> {CHANNEL_ID}\n\n"
                f"✅ <i>Successfully added to database and live in channel!</i>",
                get_inline_keyboard()
            )
        else:
            send_admin_message(
                f"❌ <b>Manual App Post Failed:</b>\n\n"
                f"📱 <b>App:</b> {html.escape(app_name)}\n"
                f"⚠️ <b>Reason:</b> <code>{html.escape(res.get('error', 'Unknown error'))}</code>",
                get_inline_keyboard()
            )
    except Exception as ex:
        import traceback
        tb = traceback.format_exc()
        send_admin_message(
            f"🚨 <b>Error Processing Custom App:</b>\n"
            f"<code>{html.escape(str(ex))}</code>\n\n"
            f"<pre>{html.escape(tb[-800:])}</pre>",
            get_inline_keyboard()
        )

def execute_pipeline_worker(trigger_source="Manual"):
    global is_pipeline_running, last_run_time, next_scheduled_run_time
    with pipeline_lock:
        if is_pipeline_running:
            return
        is_pipeline_running = True

    live_msg_id = None
    try:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{trigger_source}] Launching LiteAPKs Pipeline...")
        
        init_live = send_admin_message(
            f"🚀 <b>LiteAPKs MOD Agent — Live Execution ({trigger_source})</b>\n\n"
            f"🔍 <i>Scanning LiteAPKs for Priority 1, 2, and 3 candidate apps...</i>"
        )
        if init_live.get('ok'):
            live_msg_id = init_live.get('result', {}).get('message_id')

        def on_status(event, data):
            nonlocal live_msg_id
            if event == 'scraping':
                if live_msg_id:
                    edit_admin_message(
                        live_msg_id,
                        f"🚀 <b>LiteAPKs MOD Agent — Live Execution ({trigger_source})</b>\n\n"
                        f"🔍 <i>{data.get('status', 'Scanning LiteAPKs...')}</i>"
                    )
            elif event == 'start':
                batch = data.get('batch', [])
                if batch:
                    lines = [
                        f"🚀 <b>LiteAPKs MOD Agent — Live Run Started ({trigger_source})</b>\n",
                        f"🔍 <i>Live scraping complete! {len(batch)} fresh candidate apps selected for this batch:</i>\n\n",
                        f"📋 <b>10 Apps Scraped & Queued for Upload:</b>"
                    ]
                    for idx, a in enumerate(batch, 1):
                        title = html.escape(html.unescape(a.get('title', 'Unknown')))
                        ver = a.get('version', '')
                        ver_str = f" <code>(v{html.escape(str(ver))})</code>" if ver and ver != 'Latest' else ""
                        p_label = a.get('priority_label', a.get('category', 'App'))
                        lines.append(f"<b>{idx}.</b> 📱 <b>{title}</b>{ver_str}\n      📂 <i>{p_label}</i>")

                    lines.append(f"\n⏳ <i>Now uploading these {len(batch)} apps to DevUploads and broadcasting to channel ({CHANNEL_ID})...</i>")
                    send_admin_message("\n".join(lines))
            elif event == 'app_start':
                idx = data.get('index', 1)
                tot = data.get('total', 10)
                title = data.get('title', 'Unknown')
                ver = data.get('version', '')
                size = data.get('size_mb', 0)
                cat = data.get('category', 'App')
                next_app = data.get('next_app', 'End of batch')
                tag = "🔄 UPDATE" if data.get('is_update') else "🆕 NEW"

                txt = (
                    f"🚀 <b>LiteAPKs MOD Agent — Live Execution ({trigger_source})</b>\n\n"
                    f"📦 <b>Current Step [{idx}/{tot}]:</b>\n"
                    f"📱 <b>{tag}:</b> <b>{html.escape(title)}</b> (v{ver})\n"
                    f"📂 <b>Category:</b> {html.escape(cat)} • <b>{size} MB</b>\n"
                    f"📥 <i>Downloading APK to server...</i>\n\n"
                    f"⏭️ <b>Next in queue:</b> {html.escape(next_app)}"
                )
                if live_msg_id:
                    edit_admin_message(live_msg_id, txt)
                else:
                    new_m = send_admin_message(txt)
                    if new_m.get('ok'):
                        live_msg_id = new_m.get('result', {}).get('message_id')

            elif event == 'app_uploading':
                title = data.get('title', 'Unknown')
                ver = data.get('version', '')
                size = data.get('size_mb', 0)
                txt = (
                    f"🚀 <b>LiteAPKs MOD Agent — Live Execution ({trigger_source})</b>\n\n"
                    f"☁️ <b>Uploading to DevUploads:</b>\n"
                    f"📱 <b>{html.escape(title)}</b> (v{ver})\n"
                    f"💾 Size: <b>{size} MB</b>\n"
                    f"🔄 <i>Multi-attempt streaming upload in progress (Max 4 retries)...</i>"
                )
                if live_msg_id:
                    edit_admin_message(live_msg_id, txt)

            elif event == 'app_link_ready':
                title = data.get('title', 'Unknown')
                ver = data.get('version', '')
                d_url = data.get('devuploads_url', '')
                send_admin_message(
                    f"✅ <b>DevUploads Link Secured!</b>\n"
                    f"📱 <b>{html.escape(title)}</b> (v{ver})\n"
                    f"🔗 <code>{d_url}</code>\n"
                    f"🗑️ <i>Local APK removed from storage.</i>\n"
                    f"📢 <i>Posting rich photo card to {CHANNEL_ID}...</i>"
                )

            elif event == 'app_posted':
                title = data.get('title', 'Unknown')
                ver = data.get('version', '')
                d_url = data.get('devuploads_url', '')
                send_admin_message(
                    f"📢 <b>Published to {CHANNEL_ID}!</b>\n"
                    f"📱 <b>{html.escape(title)}</b> (v{ver})\n"
                    f"🔗 <a href='{d_url}'>DevUploads Download Link</a>"
                )

            elif event == 'done':
                proc = data.get('processed', 0)
                tot_db = data.get('total_db', 0)
                completed = data.get('completed_apps', [])

                lines = [
                    f"🎉 <b>Batch Complete! All {proc} APKs Published to {CHANNEL_ID} ({trigger_source})</b>\n",
                    f"✅ <b>All {proc} apps were successfully uploaded to DevUploads and posted to the channel!</b>\n",
                    "📋 <b>Published Apps Summary:</b>"
                ]
                for i, app in enumerate(completed, 1):
                    u = app.get('devuploads_url', '')
                    link_part = f" — <a href='{u}'>DevUploads Link</a>" if u else ""
                    lines.append(f"{i}. 📱 <b>{html.escape(app['title'])}</b> (v{app.get('version', '')}){link_part}")

                interval_sec = get_interval_minutes() * 60
                sched_target = time.strftime('%H:%M:%S IST', time.localtime(time.time() + interval_sec))
                lines.append(f"\n📦 <b>Total Database:</b> <b>{tot_db}</b> APKs recorded")
                lines.append(f"⏳ <b>Next Run Countdown:</b> <code>{get_interval_display()}</code> (at {sched_target})")

                send_admin_message("\n".join(lines), get_inline_keyboard())

        res_count = agent.run_pipeline(status_callback=on_status)
        last_run_time = time.time()
        next_scheduled_run_time = last_run_time + (get_interval_minutes() * 60)
        print(f"[{trigger_source}] Pipeline finished. Broadcasted {res_count} APKs.")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[Pipeline Error] {e}\n{tb}")
        err_msg = (
            f"🚨 <b>LiteAPKs Pipeline Error ({trigger_source}):</b>\n\n"
            f"<code>{html.escape(str(e))}</code>\n\n"
            f"<pre>{html.escape(tb[-1000:])}</pre>"
        )
        send_admin_message(err_msg, get_inline_keyboard())
    finally:
        with pipeline_lock:
            is_pipeline_running = False
            last_run_time = time.time()
            next_scheduled_run_time = last_run_time + (get_interval_minutes() * 60)
        agent.cleanup_storage()

def trigger_manual_run(chat_id=None):
    global is_pipeline_running, next_scheduled_run_time
    if is_pipeline_running:
        send_admin_message("⚠️ <b>Pipeline is already running!</b>\n\nPlease wait for the current execution to complete.", get_inline_keyboard())
        return

    send_admin_message(
        "⏳ <b>Manual Scrape & Post Initiated!</b>\n\n"
        "Scraping LiteAPKs for high-priority apps and games. You will receive live progress updates here as each APK is published...",
        None
    )
    next_scheduled_run_time = time.time() + (get_interval_minutes() * 60)
    t = threading.Thread(target=execute_pipeline_worker, args=("Manual Trigger",), daemon=True)
    t.start()

def auto_scheduler_loop():
    """Dynamic interval scheduler running independently in background with live countdown timer."""
    global next_scheduled_run_time
    cur_mins = get_interval_minutes()
    print(f"⏰ [Scheduler] Auto-Scheduler started! Active Interval: {get_interval_display(cur_mins)}")
    
    if not next_scheduled_run_time or next_scheduled_run_time <= time.time():
        next_scheduled_run_time = time.time() + (cur_mins * 60)
        
    trigger_file = os.path.join(os.path.dirname(__file__), "trigger.run")
    if os.path.exists(trigger_file):
        try:
            os.remove(trigger_file)
        except Exception:
            pass
        if not is_pipeline_running:
            print("🚀 [Trigger] 'trigger.run' detected on boot! Launching immediate pipeline run...")
            threading.Thread(target=execute_pipeline_worker, args=("Immediate Catch-Up Trigger",), daemon=True).start()
    else:
        time.sleep(15)  # Initial 15s grace period on boot
    
    while True:
        try:
            if os.path.exists(trigger_file):
                try:
                    os.remove(trigger_file)
                except Exception:
                    pass
                if not is_pipeline_running:
                    print("🚀 [Trigger] 'trigger.run' detected! Launching immediate pipeline run...")
                    threading.Thread(target=execute_pipeline_worker, args=("Immediate Catch-Up Trigger",), daemon=True).start()

            now = time.time()
            if now >= next_scheduled_run_time:
                interval_mins = get_interval_minutes()
                if not is_pipeline_running:
                    print(f"⏰ [Scheduler] {interval_mins}-Minute interval reached. Starting automated run...")
                    threading.Thread(target=execute_pipeline_worker, args=(f"{interval_mins}m Auto Schedule",), daemon=True).start()
                else:
                    print("⏰ [Scheduler] Pipeline already running, skipping scheduled tick.")
                
                next_scheduled_run_time = time.time() + (get_interval_minutes() * 60)
            
            time.sleep(3)  # Fast check so interval adjustments via Telegram take effect immediately!
        except Exception as se:
            print(f"[Scheduler Exception]: {se}")
            time.sleep(10)

def main():
    print("==================================================================")
    print("🤖 LiteAPKs MOD Agent — Interactive Telegram Daemon & 30m Scheduler")
    print(f"Bot API: https://api.telegram.org/bot{BOT_TOKEN[:10]}...")
    print(f"Target Channel: {CHANNEL_ID}")
    print(f"Admin Chat ID: {ADMIN_CHAT_ID}")
    print("==================================================================")

    # 0. Windows Sleep / Standby Prevention (Keeps bot & scheduler awake 24/7)
    if sys.platform == 'win32':
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            print("⚡ [Power Management] Windows Sleep Prevention Active (System kept awake 24/7).")
        except Exception as pe:
            print(f"[Power Management Warning]: {pe}")

    # 1. Proactive storage cleanup
    agent.cleanup_storage()

    # 2. Register Telegram Commands Menu
    register_bot_commands()

    # 2. Start Background 30-minute scheduler
    sched_thread = threading.Thread(target=auto_scheduler_loop, daemon=True)
    sched_thread.start()

    # 3. Synchronize update offset on boot to skip stale queries
    last_update_id = 0
    try:
        init_res = requests.get(f"{API_BASE}/getUpdates?offset=-1", timeout=10).json()
        if init_res.get('ok') and init_res.get('result'):
            last_update_id = init_res['result'][-1]['update_id']
            print(f"[Init] Fast-forwarded update_id offset to {last_update_id}")
    except Exception as e:
        print(f"[Init] Offset check error: {e}")

    # 4. Send Online Notification & Keyboards to Admin
    boot_msg = (
        f"🟢 <b>LiteAPKs MOD Agent is Online & Active!</b>\n\n"
        f"• <b>Target Channel:</b> {CHANNEL_ID}\n"
        f"• <b>Auto-Scheduler:</b> 🟢 Running (Every {get_interval_display()})\n"
        f"• <b>Live Step-by-Step Updates:</b> 🟢 Enabled\n"
        f"• <b>Manual Custom App Post:</b> 🟢 Ready\n"
        f"• <b>Next Run Countdown:</b> 🟢 Active\n\n"
        f"👇 <i>Use the buttons below or the bottom keyboard to control the agent:</i>"
    )
    send_tg('sendMessage', {
        'chat_id': ADMIN_CHAT_ID,
        'text': "⌨️ <i>Persistent Control Keyboard Activated.</i>",
        'parse_mode': 'HTML',
        'reply_markup': json.dumps(get_reply_keyboard())
    })
    send_admin_message(boot_msg, get_inline_keyboard())

    # 5. Long-polling Telegram event loop
    while True:
        try:
            params = {'timeout': 20, 'offset': last_update_id + 1}
            url = f"{API_BASE}/getUpdates"
            resp = requests.get(url, params=params, timeout=25)
            
            if resp.status_code != 200:
                time.sleep(2)
                continue

            data = resp.json()
            if not data.get('ok'):
                time.sleep(2)
                continue

            for update in data.get('result', []):
                last_update_id = update['update_id']

                # Handle Inline Button Callback Queries
                if 'callback_query' in update:
                    cb = update['callback_query']
                    cb_id = cb['id']
                    cb_data = cb.get('data', '')
                    from_user = cb.get('from', {})
                    user_id = str(from_user.get('id', ''))
                    msg_obj = cb.get('message', {})
                    msg_id = msg_obj.get('message_id')

                    print(f"[{time.strftime('%H:%M:%S')}] [Callback] Button '{cb_data}' clicked by user {user_id}")

                    # Allow any user to verify channel subscription for locked files
                    if cb_data.startswith('verify_sub_'):
                        file_code = cb_data.replace('verify_sub_', '')
                        handle_sub_verification(cb_id, user_id, file_code, from_user)
                        continue

                    # Admin authorization check
                    if user_id != str(ADMIN_CHAT_ID):
                        answer_callback(cb_id, "⛔ Unauthorized access!", show_alert=True)
                        continue

                    if cb_data == 'run_now':
                        answer_callback(cb_id, "🚀 Starting scraper & posting now!", show_alert=True)
                        trigger_manual_run(user_id)
                    elif cb_data == 'custom_app':
                        answer_callback(cb_id, "➕ Type the App Name to post:", show_alert=False)
                        handle_custom_app_prompt(user_id)
                    elif cb_data == 'set_interval':
                        answer_callback(cb_id, "⚙️ Set Auto-Run Interval", show_alert=False)
                        handle_set_interval_prompt(user_id, message_id=msg_id)
                    elif cb_data.startswith('set_int_'):
                        mins_str = cb_data.replace('set_int_', '')
                        try:
                            mins = int(mins_str)
                            answer_callback(cb_id, f"✅ Interval set to {mins} minutes!", show_alert=False)
                            apply_new_interval(mins, user_id, message_id=msg_id)
                        except Exception as e:
                            answer_callback(cb_id, f"Error: {e}", show_alert=True)
                    elif cb_data == 'timer':
                        now = time.time()
                        rem = max(0, int(next_scheduled_run_time - now))
                        mins = rem // 60
                        secs = rem % 60
                        answer_callback(cb_id, f"⏳ Next run in {mins:02d}m {secs:02d}s", show_alert=False)
                        handle_timer(user_id, message_id=msg_id)
                    elif cb_data == 'stats':
                        db_items = agent.load_sent_history()
                        answer_callback(cb_id, f"📊 Database: {len(db_items)} APKs", show_alert=False)
                        handle_stats(user_id, message_id=msg_id)
                    elif cb_data == 'preview':
                        answer_callback(cb_id, "🔍 Scanning priority candidate apps...", show_alert=False)
                        handle_preview(user_id)
                    elif cb_data == 'refresh':
                        answer_callback(cb_id, "🔄 Dashboard refreshed!", show_alert=False)
                        new_text = build_dashboard_text(from_user)
                        if msg_id:
                            edit_admin_message(msg_id, new_text, get_inline_keyboard())
                        else:
                            send_admin_message(new_text, get_inline_keyboard())

                # Handle Standard Text Messages / Commands / Keyboard Buttons
                elif 'message' in update:
                    msg = update['message']
                    chat_id = str(msg.get('chat', {}).get('id', ''))
                    from_user = msg.get('from', {})
                    user_id = str(from_user.get('id', ''))
                    text = msg.get('text', '').strip()

                    print(f"[{time.strftime('%H:%M:%S')}] [Message] Text '{text}' from user {user_id}")

                    # 1. Zero-Disk File Upload (Any user or admin sending a file gets a permanent cloud link)
                    has_file = any(k in msg for k in ['document', 'video', 'audio', 'photo', 'voice'])
                    if has_file:
                        handle_file_upload(msg, from_user)
                        continue

                    # 2. Deep-Link File Request (e.g. /start file_xxxx)
                    if text.startswith('/start file_') or text.startswith('/start file-'):
                        file_code = text.split()[1].replace('file_', '').replace('file-', '').strip()
                        handle_file_request(chat_id, user_id, file_code, from_user)
                        continue

                    # 3. Public Users (Non-Admin): Show Public Cloud Storage Welcome & Help
                    if user_id != str(ADMIN_CHAT_ID):
                        handle_public_start(chat_id, from_user)
                        continue

                    lower_text = text.lower()

                    # 1. Check if user is in awaiting custom app input state
                    if user_states.get(user_id) == 'awaiting_custom_app' and not text.startswith('/'):
                        user_states.pop(user_id, None)
                        threading.Thread(target=process_custom_app_worker, args=(text, chat_id), daemon=True).start()
                        continue

                    # 1b. Check if user is in awaiting interval input state
                    if user_states.get(user_id) == 'awaiting_interval' and not text.startswith('/'):
                        user_states.pop(user_id, None)
                        apply_new_interval(text, chat_id)
                        continue

                    # 1c. Command: /setinterval <minutes> or /interval <minutes>
                    if lower_text.startswith('/setinterval') or lower_text.startswith('/interval') or lower_text.startswith('/settimer'):
                        user_states.pop(user_id, None)
                        parts = text.split(maxsplit=1)
                        if len(parts) >= 2:
                            apply_new_interval(parts[1], chat_id)
                        else:
                            handle_set_interval_prompt(chat_id)
                        continue

                    # 2. Command: /postapp <app name> [optional version]
                    if lower_text.startswith('/postapp ') or lower_text.startswith('/app '):
                        query_arg = text.split(maxsplit=1)[1]
                        threading.Thread(target=process_custom_app_worker, args=(query_arg, chat_id), daemon=True).start()
                        continue

                    # 3. Standard Commands & Keyboard buttons
                    if lower_text in ['/start', '/menu', 'menu', 'start', '🔄 refresh dashboard', 'refresh']:
                        user_states.pop(user_id, None)
                        handle_start(chat_id, from_user)
                    elif lower_text in ['/post', '/run', 'post', 'run', '/start_scraping', '🚀 scrape & post now']:
                        user_states.pop(user_id, None)
                        trigger_manual_run(chat_id)
                    elif lower_text in ['/custom_app', '➕ post custom app', 'post custom app', 'add app']:
                        handle_custom_app_prompt(chat_id)
                    elif lower_text in ['/setinterval', '⚙️ set interval', 'set interval', 'change interval', 'timer setting', 'interval']:
                        handle_set_interval_prompt(chat_id)
                    elif lower_text in ['/timer', 'timer', 'countdown', '⏱️ next run countdown', '/countdown', '/schedule']:
                        handle_timer(chat_id)
                    elif lower_text in ['/stats', 'stats', 'status', '📊 database stats']:
                        handle_stats(chat_id)
                    elif lower_text in ['/preview', 'preview', '🔍 preview next apps']:
                        handle_preview(chat_id)
                    elif lower_text in ['/help', 'help']:
                        help_msg = (
                            f"📖 <b>LiteAPKs MOD Agent — Complete Guide:</b>\n\n"
                            f"• <b>🚀 Scrape & Post Now:</b> Immediately scrapes priority apps and posts 10 APKs to {CHANNEL_ID}.\n"
                            f"• <b>➕ Post Custom App:</b> Search any app on LiteAPKs (e.g. <code>CapCut</code> or <code>Spotify 8.9.18</code>) and publish it immediately!\n"
                            f"• <b>⏱️ Next Run Countdown:</b> Shows real-time countdown to the next scheduled run.\n"
                            f"• <b>⚙️ Set Interval:</b> Change auto-run timing in minutes (e.g. 60, 45, 30, 90, 120 min).\n"
                            f"• <b>🔍 Preview Next Apps:</b> Displays the top candidate apps queued for the next batch.\n"
                            f"• <b>📊 Database Stats:</b> Shows total database size and engine health.\n"
                            f"• <b>🔄 Refresh Dashboard:</b> Refreshes control panel.\n\n"
                            f"💡 <i>Quick Command:</i> Type <code>/postapp CapCut</code> anytime to publish CapCut directly!"
                        )
                        send_admin_message(help_msg, get_inline_keyboard())
                    else:
                        handle_start(chat_id, from_user)

        except requests.exceptions.RequestException as re_err:
            print(f"[Network/Polling Timeout]: {re_err}")
            time.sleep(2)
        except Exception as e:
            print(f"[Bot Polling Exception]: {e}")
            time.sleep(2)

if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print("[Shutdown] User stopped LiteAPKs MOD Agent.")
            break
        except Exception as e:
            print(f"[Supervisor Error]: {e}. Automatically restarting daemon in 5s...")
            time.sleep(5)
