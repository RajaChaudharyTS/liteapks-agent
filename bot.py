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

def send_tg(method, payload, files=None):
    try:
        url = f"{API_BASE}/{method}"
        res = requests.post(url, data=payload, files=files, timeout=25)
        return res.json()
    except Exception as e:
        print(f"[Telegram API Error] {method}: {e}")
        return {}

def send_admin_message(text, reply_markup=None):
    if not ADMIN_CHAT_ID:
        return
    payload = {
        'chat_id': ADMIN_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    return send_tg('sendMessage', payload)

def answer_callback(callback_query_id, text=None):
    payload = {'callback_query_id': callback_query_id}
    if text:
        payload['text'] = text
    return send_tg('answerCallbackQuery', payload)

def get_control_keyboard():
    return {
        'inline_keyboard': [
            [
                {'text': '🚀 तुरंत पोस्ट करो (Post Now)', 'callback_data': 'run_now'}
            ],
            [
                {'text': '📊 डेटाबेस स्टेटस', 'callback_data': 'stats'},
                {'text': '🔍 नए ऐप्स प्रीव्यू', 'callback_data': 'preview'}
            ],
            [
                {'text': '⏱️ 30-Min ऑटो-शेड्यूल स्टेटस', 'callback_data': 'schedule_info'}
            ]
        ]
    }

def handle_start(chat_id, user_info):
    db_items = agent.load_sent_history()
    status_str = "🟡 सक्रिय (पोस्टिंग जारी है)" if is_pipeline_running else "🟢 रेडी (आइडल)"
    last_str = time.strftime('%H:%M:%S IST', time.localtime(last_run_time)) if last_run_time else "अभी तक नहीं"
    
    msg = (
        f"🤖 <b>LiteAPKs MOD Agent — Control Panel</b>\n\n"
        f"👑 <b>Admin:</b> {html.escape(user_info.get('first_name', 'Admin'))} (<code>{chat_id}</code>)\n"
        f"📢 <b>टारगेट चैनल:</b> {CHANNEL_ID}\n"
        f"📦 <b>कुल पोस्टेड डेटाबेस:</b> <b>{len(db_items)}</b> APKs\n"
        f"⏱️ <b>ऑटो-शेड्यूल:</b> <b>हर 30 मिनट</b> (बैकग्राउंड एक्टिव)\n"
        f"🕒 <b>पिछला रन:</b> {last_str}\n"
        f"⚡ <b>करंट स्टेटस:</b> {status_str}\n\n"
        f"👇 <i>नीचे दिए गए बटन पर टैप करके आप तुरंत मैन्युअल रन शुरू कर सकते हैं:</i>"
    )
    send_admin_message(msg, get_control_keyboard())

def handle_stats(chat_id):
    db_items = agent.load_sent_history()
    status_str = "🟡 रनिंग" if is_pipeline_running else "🟢 रेडी"
    msg = (
        f"📊 <b>LiteAPKs Agent आँकड़े:</b>\n\n"
        f"• <b>डेटाबेस साइज़:</b> {len(db_items)} APKs\n"
        f"• <b>चैनल:</b> {CHANNEL_ID}\n"
        f"• <b>अधिकतम ऐप्स प्रति रन:</b> {MAX_APKS_PER_RUN}\n"
        f"• <b>करंट स्टेटस:</b> {status_str}\n"
        f"• <b>30-मिनट शेड्यूलर:</b> 🟢 एक्टिव (हर 30 मिनट पर ऑटोमैटिक पोस्टिंग)"
    )
    send_admin_message(msg, get_control_keyboard())

def handle_preview(chat_id):
    send_admin_message("🔍 <i>LiteAPKs से टॉप ट्रेंडिंग ऐप्स स्कैन किए जा रहे हैं...</i>")
    try:
        urls = agent.scrape_latest_apk_links()
        sent = agent.load_sent_history()
        candidates = [u for u in urls if u not in sent][:6]
        
        if not candidates:
            send_admin_message("ℹ️ सभी मुख्य ऐप्स पहले से ही पोस्टेड हैं। नए ऐप्स आते ही पोस्ट होंगे।", get_control_keyboard())
            return

        preview_lines = []
        for idx, u in enumerate(candidates, 1):
            name = u.rstrip('/').split('/')[-1].replace('.html', '').replace('-', ' ').title()
            preview_lines.append(f"{idx}. <b>{name}</b>\n   <code>{u}</code>")

        msg = (
            f"🔍 <b>अगले रन के टॉप 5-6 संभावित ऐप्स (Preview):</b>\n\n" +
            "\n\n".join(preview_lines) +
            "\n\n👉 <i>तुरंत पोस्ट करने के लिए नीचे 'तुरंत पोस्ट करो' दबाएं।</i>"
        )
        send_admin_message(msg, get_control_keyboard())
    except Exception as e:
        send_admin_message(f"⚠️ प्रीव्यू फ़ेच करने में एरर: {e}", get_control_keyboard())

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
                line = f"📦 [<code>{proc}/{tot}</code>] <b>{tag}:</b> <b>{html.escape(title)}</b> (v{ver})\n<i>Direct APK / DevUploads • {size} MB</i>"
                send_admin_message(line)
            elif event == 'done':
                proc = data.get('processed', 0)
                tot_db = data.get('total_db', 0)
                msg = (
                    f"🎉 <b>सफलतापूर्वक पोस्टिंग पूर्ण! ({trigger_source})</b>\n\n"
                    f"✅ कुल <b>{proc}</b> नए/अपडेटेड जरूरी APKs चैनल {CHANNEL_ID} पर पोस्ट कर दिए गए हैं।\n"
                    f"📦 डेटाबेस में अब कुल <b>{tot_db}</b> APKs सुरक्षित हैं।\n"
                    f"⏱️ अगला रन 30 मिनट बाद ऑटोमैटिक होगा।"
                )
                send_admin_message(msg, get_control_keyboard())

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
        send_admin_message(err_msg, get_control_keyboard())
    finally:
        with pipeline_lock:
            is_pipeline_running = False

def trigger_manual_run():
    global is_pipeline_running
    if is_pipeline_running:
        send_admin_message("⚠️ <b>पाइपलाइन पहले से ही सक्रिय है!</b>\n\nकृपया मौजूदा रन पूरा होने की प्रतीक्षा करें।", get_control_keyboard())
        return

    send_admin_message(
        "⏳ <b>मैन्युअल रन शुरू हो गया है!</b>\n\n"
        "LiteAPKs से टॉप और जरूरी APKs स्क्रैप किए जा रहे हैं। जैसे-जैसे ऐप्स चैनल पर पोस्ट होंगे, आपको यहाँ लाइव प्रोग्रेस मिलती रहेगी...",
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

    # 1. Start Background 30-minute scheduler
    sched_thread = threading.Thread(target=auto_scheduler_loop, daemon=True)
    sched_thread.start()

    # 2. Send Online Notification to Admin
    boot_msg = (
        f"🟢 <b>LiteAPKs MOD Agent ऑनलाइन है!</b>\n\n"
        f"• <b>टारगेट चैनल:</b> {CHANNEL_ID}\n"
        f"• <b>30-मिनट ऑटो-शेड्यूलर:</b> एक्टिव\n"
        f"• <b>मैन्युअल कंट्रोल:</b> एक्टिव\n\n"
        f"👇 नीचे दिए गए मेनू से आप कभी भी 1-क्लिक में पोस्ट शुरू कर सकते हैं:"
    )
    send_admin_message(boot_msg, get_control_keyboard())

    last_update_id = 0

    # 3. Long-polling Telegram event loop
    while True:
        try:
            params = {'timeout': 25, 'offset': last_update_id + 1}
            url = f"{API_BASE}/getUpdates"
            resp = requests.get(url, params=params, timeout=30)
            
            if resp.status_code != 200:
                time.sleep(3)
                continue

            data = resp.json()
            if not data.get('ok'):
                time.sleep(3)
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

                    # Admin authorization check
                    if user_id != str(ADMIN_CHAT_ID):
                        answer_callback(cb_id, "⛔ अनधिकृत एक्सेस!")
                        continue

                    if cb_data == 'run_now':
                        answer_callback(cb_id, "🚀 रन शुरू हो रहा है...")
                        trigger_manual_run()
                    elif cb_data == 'stats':
                        answer_callback(cb_id)
                        handle_stats(user_id)
                    elif cb_data == 'preview':
                        answer_callback(cb_id)
                        handle_preview(user_id)
                    elif cb_data == 'schedule_info':
                        answer_callback(cb_id, "⏱️ शेड्यूलर हर 30 मिनट में ऑटोमैटिक पोस्ट करता है।")
                        handle_stats(user_id)

                # Handle Standard Text Messages / Commands
                elif 'message' in update:
                    msg = update['message']
                    chat_id = str(msg.get('chat', {}).get('id', ''))
                    from_user = msg.get('from', {})
                    user_id = str(from_user.get('id', ''))
                    text = msg.get('text', '').strip()

                    # Admin authorization check
                    if user_id != str(ADMIN_CHAT_ID):
                        send_tg('sendMessage', {
                            'chat_id': chat_id,
                            'text': "⛔ <b>Access Denied:</b> This bot is strictly reserved for the Administrator of @getmodpc."
                        })
                        continue

                    lower_text = text.lower()
                    if lower_text in ['/start', '/menu', 'menu', 'start']:
                        handle_start(chat_id, from_user)
                    elif lower_text in ['/post', '/run', 'post', 'run', '/start_scraping']:
                        trigger_manual_run()
                    elif lower_text in ['/stats', 'stats', 'status']:
                        handle_stats(chat_id)
                    elif lower_text in ['/preview', 'preview']:
                        handle_preview(chat_id)
                    else:
                        handle_start(chat_id, from_user)

        except requests.exceptions.RequestException as re_err:
            time.sleep(3)
        except Exception as e:
            print(f"[Bot Polling Exception]: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
