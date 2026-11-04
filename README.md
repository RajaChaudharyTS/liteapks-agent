# 📱 LiteAPKs to Telegram & DevUploads Autonomous Agent

An autonomous AI agent that monitors `liteapks.com` for newly published and updated Android APKs, automatically uploads the `.apk` file to **DevUploads** (with Pay-Per-Download monetization), extracts clean metadata and high-resolution posters, and broadcasts them to your Telegram Channel (`@your_channel_username`) with direct download buttons and viral hashtags!

---

## 🌟 Key Advantages

1. **💰 100% Monetized Downloads (PPD Earnings):**
   * Uploads every APK directly to your **DevUploads** account via API.
   * Every download generates revenue in USD ($).
2. **🚀 Bypasses Telegram 50 MB Bot Limit:**
   * Standard Telegram Bots fail on files > 50 MB.
   * DevUploads supports files up to multiple GBs, so heavy 100MB–1.5GB games (GTA, Call of Duty, Spotify, CapCut) are effortlessly supported.
3. **🛡️ Shields Telegram Channel from DMCA / Copyright Bans:**
   * Hosting cracked `.apk` files directly inside Telegram chats carries high risk of channel bans.
   * Sharing clean preview cards with off-site download links keeps your channel 100% safe.
4. **🏷️ Dynamic Smart Hashtags:**
   * Auto-generates relevant tags (`#Spotify #MusicPlayer #ModAPK #Android #PremiumUnlocked #YourChannelTag`) for top ranking in Telegram Search.
5. **🔒 100% Private & Secure (Zero Leaks):**
   * All API keys and tokens are loaded strictly from `.env` (locally) or GitHub Actions Secrets (in the cloud).
   * `.env` is `.gitignore`d so no secrets are ever committed or pushed to Git.

---

## 🚀 Setup & Free 24/7 Deployment (GitHub Actions)

### Step 1: Push to GitHub
Push this directory to a GitHub repository (Public or Private):
```bash
git init
git add .
git commit -m "feat: LiteAPKs DevUploads Telegram Agent"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```
*(Notice: Your `.env` file is automatically ignored and will never be pushed).*

### Step 2: Add GitHub Secrets (Zero Exposure)
In your GitHub repository:
1. Go to **Settings > Secrets and variables > Actions**.
2. Click **New repository secret** and add:
   * `TELEGRAM_BOT_TOKEN`: *(Your Telegram Bot Token)*
   * `TELEGRAM_CHANNEL_ID`: `@your_channel_or_id` *(Or your channel @username)*
   * `ADMIN_CHAT_ID`: `your_telegram_user_id`
   * `DEVUPLOADS_API_KEY`: *(Your DevUploads API Key)*

### Step 3: 24/7 Automation Active!
* Go to the **Actions** tab on GitHub.
* The workflow `.github/workflows/liteapks_sync.yml` is scheduled to run every **30 minutes** 24/7.
* It automatically checks for new apps, uploads them to DevUploads, posts to your Telegram channel, and commits the updated history back to `sent_apks.json`.

---

## 💻 Local Testing & Manual Runs
1. Copy `.env.example` to `.env` and fill in your keys.
2. Run:
   ```bash
   pip install -r requirements.txt
   python agent.py
   ```
*(Or double-click `run_local.bat` on Windows).*
