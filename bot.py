import os
import sys
import time
import json
import html
import threading
import requests

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
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

# Thread-safe pipeline state
is_pipeline_running = False
pipeline_lock = threading.Lock()
last_run_time = None

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
    if not ADMIN_CHAT_ID:
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
    return {
        'inline_keyboard': [
            [
                {'text': '🚀 Scrape & Post Now', 'callback_data': 'run_now'}
            ],
            [
                {'text': '📊 Database Stats', 'callback_data': 'stats'},
                {'text': '🔍 Preview Next Apps', 'callback_data': 'preview'}
            ],
            [
                {'text': '⏱️ 30-Min Schedule Status', 'callback_data': 'schedule_info'},
                {'text': '🔄 Refresh Dashboard', 'callback_data': 'refresh'}
            ]
        ]
    }

def get_reply_keyboard():
    """Persistent bottom keyboard on mobile/desktop for 100% reliable 1-tap interaction."""
    return {
        'keyboard': [
            [
                {'text': '🚀 Scrape & Post Now'}
            ],
            [
                {'text': '📊 Database Stats'},
                {'text': '🔍 Preview Next Apps'}
            ],
            [
                {'text': '⏱️ Schedule Status'},
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
        {'command': 'stats', 'description': 'View Database & Agent Stats'},
        {'command': 'preview', 'description': 'Preview Next Priority Apps'},
        {'command': 'schedule', 'description': '30-Minute Schedule Status'},
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
    
    return (
        f"🤖 <b>LiteAPKs MOD Agent — Control Panel</b>\n\n"
        f"👑 <b>Admin:</b> {html.escape(name)} (<code>{ADMIN_CHAT_ID}</code>)\n"
        f"📢 <b>Target Channel:</b> {CHANNEL_ID}\n"
        f"📦 <b>Broadcasted Database:</b> <b>{len(db_items)}</b> APKs\n"
        f"⏱️ <b>Auto-Schedule:</b> <b>Every 30 Minutes</b> (Autonomous Background)\n"
        f"🕒 <b>Last Run:</b> {last_str}\n"
        f"⚡ <b>Engine Status:</b> {status_str}\n\n"
        f"👇 <i>Tap any button below or in the persistent bottom menu:</i>"
    )

def handle_start(chat_id, user_info=None):
    msg = build_dashboard_text(user_info)
    # First send with persistent reply keyboard so bottom bar stays active
    send_tg('sendMessage', {
        'chat_id': chat_id,
        'text': "⌨️ <i>Persistent control keyboard enabled.</i>",
        'parse_mode': 'HTML',
        'reply_markup': json.dumps(get_reply_keyboard())
    })
    # Then send interactive dashboard with inline buttons
    send_admin_message(msg, get_inline_keyboard())

def handle_stats(chat_id, message_id=None):
    db_items = agent.load_sent_history()
    status_str = "🟡 Scraping / Uploading" if is_pipeline_running else "🟢 Ready / Idle"
    last_str = time.strftime('%H:%M:%S IST', time.localtime(last_run_time)) if last_run_time else "None yet"
    
    msg = (
        f"📊 <b>LiteAPKs MOD Agent Statistics:</b>\n\n"
        f"• <b>Total Database:</b> <b>{len(db_items)}</b> APKs recorded\n"
        f"• <b>Target Channel:</b> {CHANNEL_ID}\n"
        f"• <b>Batch Limit:</b> {MAX_APKS_PER_RUN} APKs per run\n"
        f"• <b>Engine Status:</b> {status_str}\n"
        f"• <b>Last Run:</b> {last_str}\n"
        f"• <b>Auto-Scheduler:</b> 🟢 Active (Every 30 Minutes)\n"
        f"• <b>Timestamp:</b> {time.strftime('%Y-%m-%d %H:%M:%S IST')}"
    )
    if message_id:
        edit_admin_message(message_id, msg, get_inline_keyboard())
    else:
        send_admin_message(msg, get_inline_keyboard())

def fast_preview_candidates():
    """Ultra-fast preview (<2s): scans Tier 1 top carousels first before deep category scanning."""
    sent = agent.load_sent_history()
    candidates = []

    # Fast Tier 1 check: Homepage, /apps, /games (only 3 quick HTTP calls)
    for root_url in [f"{agent.SOURCE_BASE_URL}/", f"{agent.SOURCE_BASE_URL}/apps", f"{agent.SOURCE_BASE_URL}/games"]:
        try:
            r = requests.get(root_url, headers=agent.HEADERS, timeout=6)
            if r.status_code == 200:
                html_text = r.text
                import re
                top_items = re.findall(r'<a[^>]+class=[\"\'][^\"\']*top-app-item[^\"\']*[\"\'][^>]+href=[\"\'](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)[\"\']', html_text)
                for u in top_items:
                    if not agent.is_banned_app("", u) and u not in sent and u not in candidates:
                        candidates.append(u)
                        if len(candidates) >= 6:
                            return candidates
        except Exception:
            pass

    # If Tier 1 didn't have 6 fresh candidates, quickly check top 5 viral categories
    top_fast_cats = [
        "https://liteapks.com/apps/video-players-editors",
        "https://liteapks.com/apps/music-audio",
        "https://liteapks.com/games/action",
        "https://liteapks.com/apps/photography",
        "https://liteapks.com/apps/tools"
    ]
    for cat_url in top_fast_cats:
        if len(candidates) >= 6:
            break
        try:
            r = requests.get(cat_url, headers=agent.HEADERS, timeout=6)
            if r.status_code == 200:
                import re
                cat_items = re.findall(r'href=[\"\'](https://liteapks\.com/[a-zA-Z0-9\-_]+\.html)[\"\']', r.text)
                for u in cat_items:
                    if not agent.is_banned_app("", u) and u not in sent and u not in candidates:
                        candidates.append(u)
                        if len(candidates) >= 6:
                            return candidates
        except Exception:
            pass

    return candidates

def preview_worker(chat_id):
    try:
        candidates = fast_preview_candidates()
        
        if not candidates:
            send_admin_message(
                "ℹ️ <b>All priority candidates are already posted!</b>\n\n"
                "The database is completely up-to-date with LiteAPKs top charts. New releases will be automatically published.",
                get_inline_keyboard()
            )
            return

        preview_lines = []
        for idx, u in enumerate(candidates[:6], 1):
            name = u.rstrip('/').split('/')[-1].replace('.html', '').replace('-', ' ').title()
            preview_lines.append(f"{idx}. <b>{name}</b>\n   <code>{u}</code>")

        msg = (
            f"🔍 <b>Next Priority Candidate Apps (Preview):</b>\n\n" +
            "\n\n".join(preview_lines) +
            "\n\n👉 <i>Tap <b>'🚀 Scrape & Post Now'</b> to publish them immediately to channel.</i>"
        )
        send_admin_message(msg, get_inline_keyboard())
    except Exception as e:
        send_admin_message(f"⚠️ Error fetching preview: {e}", get_inline_keyboard())

def handle_preview(chat_id):
    threading.Thread(target=preview_worker, args=(chat_id,), daemon=True).start()

def execute_pipeline_worker(trigger_source="Manual"):
    global is_pipeline_running, last_run_time
    with pipeline_lock:
        if is_pipeline_running:
            return
        is_pipeline_running = True

    try:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{trigger_source}] Launching LiteAPKs Pipeline...")
        
        def on_status(event, data):
            if event == 'progress':
                title = data.get('title', 'Unknown')
                ver = data.get('version', '')
                proc = data.get('processed', 0)
                tot = data.get('max', 10)
                size = data.get('size_mb', 0)
                tag = "🔄 UPDATE" if data.get('is_update') else "🆕 NEW"
                line = (
                    f"📦 [<code>{proc}/{tot}</code>] <b>{tag}:</b> <b>{html.escape(title)}</b> (v{ver})\n"
                    f"<i>Direct APK / DevUploads • {size} MB • Sent to {CHANNEL_ID}</i>"
                )
                send_admin_message(line)
            elif event == 'done':
                proc = data.get('processed', 0)
                tot_db = data.get('total_db', 0)
                msg = (
                    f"🎉 <b>Posting Completed Successfully! ({trigger_source})</b>\n\n"
                    f"✅ Published <b>{proc}</b> new/updated APKs to channel {CHANNEL_ID}.\n"
                    f"📦 Total database count: <b>{tot_db}</b> APKs.\n"
                    f"⏱️ Next automated run scheduled in 30 minutes."
                )
                send_admin_message(msg, get_inline_keyboard())

        res_count = agent.run_pipeline(status_callback=on_status)
        last_run_time = time.time()
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

def trigger_manual_run(chat_id=None):
    global is_pipeline_running
    if is_pipeline_running:
        send_admin_message("⚠️ <b>Pipeline is already running!</b>\n\nPlease wait for the current execution to complete.", get_inline_keyboard())
        return

    send_admin_message(
        "⏳ <b>Manual Scrape & Post Initiated!</b>\n\n"
        "Scraping LiteAPKs for high-priority apps and games. You will receive live progress updates here as each APK is published...",
        None
    )
    t = threading.Thread(target=execute_pipeline_worker, args=("Manual Trigger",), daemon=True)
    t.start()

def auto_scheduler_loop():
    """Guaranteed 30-minute interval scheduler running independently in background."""
    print("⏰ [Scheduler] 30-Minute Guaranteed Interval Scheduler started!")
    time.sleep(30) # Initial 30s grace period on boot
    while True:
        try:
            # Wait 30 minutes (1800 seconds)
            time.sleep(1800)
            if not is_pipeline_running:
                print("⏰ [Scheduler] 30-Minute interval reached. Starting automated run...")
                execute_pipeline_worker(trigger_source="30-Min Auto Schedule")
            else:
                print("⏰ [Scheduler] Pipeline already running, skipping scheduled tick.")
        except Exception as se:
            print(f"[Scheduler Exception]: {se}")
            time.sleep(60)

def main():
    print("==================================================================")
    print("🤖 LiteAPKs MOD Agent — Interactive Telegram Daemon & 30m Scheduler")
    print(f"Bot API: https://api.telegram.org/bot{BOT_TOKEN[:10]}...")
    print(f"Target Channel: {CHANNEL_ID}")
    print(f"Admin Chat ID: {ADMIN_CHAT_ID}")
    print("==================================================================")

    # 1. Register Telegram Commands Menu
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
        f"• <b>30-Min Auto-Scheduler:</b> 🟢 Running\n"
        f"• <b>Interactive Control Panel:</b> Ready\n\n"
        f"👇 <i>Use the buttons below or the bottom keyboard to control the agent:</i>"
    )
    # Enable persistent reply keyboard
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

                    # Admin authorization check
                    if user_id != str(ADMIN_CHAT_ID):
                        answer_callback(cb_id, "⛔ Unauthorized access!", show_alert=True)
                        continue

                    if cb_data == 'run_now':
                        answer_callback(cb_id, "🚀 Starting scraper & posting now!", show_alert=True)
                        trigger_manual_run(user_id)
                    elif cb_data == 'stats':
                        db_items = agent.load_sent_history()
                        answer_callback(cb_id, f"📊 Database: {len(db_items)} APKs | Engine: {'Running' if is_pipeline_running else 'Ready'}", show_alert=False)
                        handle_stats(user_id, message_id=msg_id)
                    elif cb_data == 'preview':
                        answer_callback(cb_id, "🔍 Scanning priority candidate apps...", show_alert=False)
                        handle_preview(user_id)
                    elif cb_data == 'schedule_info':
                        answer_callback(cb_id, "⏱️ Auto-Scheduler runs every 30 minutes in the background.", show_alert=True)
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

                    # Admin authorization check
                    if user_id != str(ADMIN_CHAT_ID):
                        send_tg('sendMessage', {
                            'chat_id': chat_id,
                            'text': "⛔ <b>Access Denied:</b> This bot is strictly reserved for the Administrator of @getmodpc."
                        })
                        continue

                    lower_text = text.lower()
                    if lower_text in ['/start', '/menu', 'menu', 'start', '🔄 refresh dashboard', 'refresh']:
                        handle_start(chat_id, from_user)
                    elif lower_text in ['/post', '/run', 'post', 'run', '/start_scraping', '🚀 scrape & post now']:
                        trigger_manual_run(chat_id)
                    elif lower_text in ['/stats', 'stats', 'status', '📊 database stats']:
                        handle_stats(chat_id)
                    elif lower_text in ['/preview', 'preview', '🔍 preview next apps']:
                        handle_preview(chat_id)
                    elif lower_text in ['/schedule', 'schedule', '⏱️ schedule status']:
                        db_items = agent.load_sent_history()
                        last_str = time.strftime('%H:%M:%S IST', time.localtime(last_run_time)) if last_run_time else "Not executed yet"
                        sched_msg = (
                            f"⏱️ <b>30-Minute Schedule Status:</b>\n\n"
                            f"• <b>Status:</b> 🟢 Running autonomously\n"
                            f"• <b>Interval:</b> Every 30 minutes\n"
                            f"• <b>Last Run:</b> {last_str}\n"
                            f"• <b>Total Database:</b> {len(db_items)} APKs posted\n"
                            f"• <b>Channel:</b> {CHANNEL_ID}\n\n"
                            f"💡 <i>You can also tap '🚀 Scrape & Post Now' anytime to force an immediate run!</i>"
                        )
                        send_admin_message(sched_msg, get_inline_keyboard())
                    elif lower_text in ['/help', 'help']:
                        help_msg = (
                            f"📖 <b>LiteAPKs MOD Agent — Quick Guide:</b>\n\n"
                            f"• <b>🚀 Scrape & Post Now:</b> Scrapes LiteAPKs and publishes top 10 new APKs directly to {CHANNEL_ID}.\n"
                            f"• <b>📊 Database Stats:</b> Shows total APKs published and engine health.\n"
                            f"• <b>🔍 Preview Next Apps:</b> Displays the top candidate apps queued for the next post.\n"
                            f"• <b>⏱️ Schedule Status:</b> Shows auto-posting timer status (runs every 30 mins).\n"
                            f"• <b>🔄 Refresh Dashboard:</b> Refreshes the control panel with live data.\n\n"
                            f"<i>Tap the buttons below or use the bottom keyboard anytime!</i>"
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
    main()
