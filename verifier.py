import os
import sys
import re
import json
import time
import html
try:
    from telethon import TelegramClient
    from telethon.tl.types import Message
except ImportError:
    TelegramClient = None
    Message = None

# Parent imports
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    ADMIN_CHAT_ID,
    DATABASE_FILE,
    CATALOG_FILE,
    TG_API_ID,
    TG_API_HASH
)

class AppSelectionVerifierSubagent:
    """
    Dedicated Intelligent Verification Sub-Agent for LiteAPKs Candidate Selection.
    
    Guarantees 100% Zero-Duplicate and Strict Version Compliance across:
    1. Local persistent database (sent_apks.json)
    2. Published catalog (apps_catalog.json)
    3. Live Telegram Channel History (read in real-time from @AndroModApps)
    4. In-batch active selection locks
    
    If any candidate has the SAME version or an OLDER version than already posted,
    it is DISCARDED immediately and replaced with a valid candidate from the priority cascade.
    """

    def __init__(self, sent_set=None, catalog=None, live_sync=True):
        self.sent_set = sent_set if sent_set is not None else set()
        self.catalog = catalog if catalog is not None else {}
        self.session_path = os.path.join(CURRENT_DIR, "user_uploader.session")
        
        # In-memory unified registry
        self.registry = {
            'by_slug': {},      # slug -> set of clean versions
            'by_pkg': {},       # package_name -> set of clean versions
            'by_title': {},     # norm_title -> set of clean versions
            'by_url': {},       # url -> set of clean versions
            'titles_catalog': {} # norm_title -> canonical slug
        }
        
        # In-batch active locks
        self.batch_slugs = set()
        self.batch_pkgs = set()
        self.batch_titles = set()
        self.batch_urls = set()

        # Step 1: Ingest database
        self._ingest_sent_set(self.sent_set)
        self._ingest_catalog(self.catalog)

        # Step 2: Ingest live channel history if enabled
        if live_sync:
            self.sync_live_channel_history(limit=250)

    # ==========================================================================
    # 🧮 NORMALIZATION HELPERS
    # ==========================================================================
    @staticmethod
    def normalize_title(title):
        if not title:
            return ""
        t = re.sub(r'\s*[\(\[].*?[\)\]]', ' ', str(title))
        # Remove version numbers from title
        t = re.sub(r'(?i)\bv?\d+(?:\.\d+)+[a-z0-9_-]*\b', ' ', t)
        t = re.sub(r'(?i)\b(?:mod|apk|premium|unlocked|vip|latest|version|for android|free download|app|apps|pro|plus)\b', ' ', t)
        t = re.sub(r'[^\w\s]', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip().lower()
        return t

    @staticmethod
    def clean_version(ver_str):
        if not ver_str:
            return ""
        v = str(ver_str).strip()
        # Handle multiple leading v's like 'vv12.2.2s'
        v = re.sub(r'^[vV]+\s*', '', v)
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

    @staticmethod
    def parse_version_tuple(v_str):
        v = AppSelectionVerifierSubagent.clean_version(v_str)
        if not v:
            return ()
        tokens = re.findall(r'(\d+|[a-zA-Z]+)', v)
        parsed = []
        for t in tokens:
            if t.isdigit():
                parsed.append(int(t))
            else:
                parsed.append(t.lower())
        # Trim trailing zeros so 2.0 == 2.0.0
        while parsed and parsed[-1] == 0:
            parsed.pop()
        return tuple(parsed)

    @staticmethod
    def compare_versions(cand_ver, prev_ver):
        """
        Returns:
           1 if cand_ver > prev_ver (strictly newer version)
           0 if cand_ver == prev_ver (identical or semantically same version)
          -1 if cand_ver < prev_ver (older version)
        """
        c_clean = AppSelectionVerifierSubagent.clean_version(cand_ver)
        p_clean = AppSelectionVerifierSubagent.clean_version(prev_ver)
        if c_clean == p_clean:
            return 0
        t_cand = AppSelectionVerifierSubagent.parse_version_tuple(cand_ver)
        t_prev = AppSelectionVerifierSubagent.parse_version_tuple(prev_ver)
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

    @staticmethod
    def get_canonical_slug(url):
        clean_u = re.sub(r'/download/?$', '', url.rstrip('/'))
        slug = clean_u.split('/')[-1].replace('.html', '').lower()
        return re.sub(r'-\d+$', '', slug)

    # ==========================================================================
    # 📥 REGISTRY INGESTION
    # ==========================================================================
    def _ingest_sent_set(self, sent_set):
        for item in sent_set:
            if item.startswith('slug_id::'):
                parts = item.split('::')
                if len(parts) >= 3:
                    s, v = parts[1], self.clean_version(parts[2])
                    if v: self.registry['by_slug'].setdefault(s, set()).add(v)
            elif item.startswith('pkg_id::'):
                parts = item.split('::')
                if len(parts) >= 3:
                    p, v = parts[1], self.clean_version(parts[2])
                    if v: self.registry['by_pkg'].setdefault(p, set()).add(v)
            elif item.startswith('id::'):
                parts = item.split('::')
                if len(parts) >= 3:
                    t, v = parts[1], self.clean_version(parts[2])
                    if v: self.registry['by_title'].setdefault(t, set()).add(v)
            elif item.startswith('https://'):
                if '::' in item:
                    u, v_raw = item.split('::', 1)
                    v = self.clean_version(v_raw)
                    s = self.get_canonical_slug(u)
                    if v:
                        self.registry['by_url'].setdefault(u, set()).add(v)
                        self.registry['by_slug'].setdefault(s, set()).add(v)
                else:
                    s = self.get_canonical_slug(item)
                    self.registry['by_url'].setdefault(item, set())
                    self.registry['by_slug'].setdefault(s, set())

    def _ingest_catalog(self, catalog):
        for slug, entry in catalog.items():
            v = self.clean_version(entry.get('version', ''))
            if v:
                self.registry['by_slug'].setdefault(slug, set()).add(v)
                pkg = entry.get('package_name', '')
                if pkg:
                    self.registry['by_pkg'].setdefault(pkg, set()).add(v)
                norm_t = self.normalize_title(entry.get('title', ''))
                if norm_t:
                    self.registry['by_title'].setdefault(norm_t, set()).add(v)
                    self.registry['titles_catalog'][norm_t] = slug
                dl_page = entry.get('download_page', '')
                if dl_page:
                    self.registry['by_url'].setdefault(dl_page, set()).add(v)

    # ==========================================================================
    # 📡 LIVE CHANNEL HISTORY SYNCHRONIZATION
    # ==========================================================================
    def sync_live_channel_history(self, limit=250):
        """Fetches the latest messages directly from the Telegram channel to synchronize ground truth."""
        if TelegramClient is None or not os.path.exists(self.session_path):
            print(f"[Verifier Subagent]: Session file {self.session_path} or Telethon not available. Relying on persistent database & catalog.")
            return

        try:
            import asyncio
            async def _fetch():
                client = TelegramClient(self.session_path, TG_API_ID, TG_API_HASH)
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    return []
                channel = int(CHANNEL_ID) if str(CHANNEL_ID).lstrip('-').isdigit() else CHANNEL_ID
                fetched = []
                async for msg in client.iter_messages(channel, limit=limit):
                    if not isinstance(msg, Message):
                        continue
                    text = msg.message or ''
                    if text:
                        lines = [l.strip() for l in text.split('\n') if l.strip()]
                        title = None
                        ver = None
                        for l in lines:
                            if '📱' in l:
                                title = l.replace('📱', '').strip()
                            elif '🏷️' in l or 'Version:' in l:
                                ver = l.replace('🏷️', '').replace('Version:', '').strip()
                        if title:
                            fetched.append({'title': title, 'ver': ver or 'Latest'})
                await client.disconnect()
                return fetched

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            channel_posts = loop.run_until_complete(_fetch())
            loop.close()

            synced_count = 0
            for post in channel_posts:
                raw_t = post['title']
                raw_v = post['ver']
                norm_t = self.normalize_title(raw_t)
                clean_v = self.clean_version(raw_v)
                
                # Derive slug from clean title
                derived_slug = re.sub(r'[^a-z0-9]+', '-', norm_t).strip('-')
                
                if norm_t and clean_v:
                    self.registry['by_title'].setdefault(norm_t, set()).add(clean_v)
                    self.sent_set.add(f"id::{norm_t}::{clean_v}")
                    synced_count += 1
                if derived_slug and clean_v:
                    self.registry['by_slug'].setdefault(derived_slug, set()).add(clean_v)
                    self.sent_set.add(f"slug_id::{derived_slug}::{clean_v}")

            print(f"📡 [Verifier Subagent Live Sync]: Successfully synced {synced_count} real-world posts from Telegram Channel {CHANNEL_ID} into memory!")
        except Exception as e:
            print(f"[Verifier Subagent Live Sync Warning]: {e}")

    # ==========================================================================
    # 🔍 CANDIDATE VERIFICATION & RE-VERIFICATION
    # ==========================================================================
    def verify_candidate(self, candidate_item, meta):
        """
        Rigorously checks whether candidate app was already posted with identical or newer version.
        
        Returns:
            (decision, reason, prev_ver)
            decision: 'ACCEPT' or 'REJECT'
            reason: Explanation string
            prev_ver: Version string of previously posted version (if any)
        """
        url = candidate_item.get('url', '')
        title = meta.get('title') or candidate_item.get('title', '')
        version = meta.get('version') or candidate_item.get('version', 'Latest')
        package_name = meta.get('package_name', '')
        c_slug = self.get_canonical_slug(url)
        norm_title = self.normalize_title(title)
        cand_ver = self.clean_version(version)

        # 1. In-Batch Duplicate Guard (Prevent selecting same app twice in one batch)
        if c_slug in self.batch_slugs:
            return 'REJECT', f"Already selected in current batch (Slug: {c_slug})", version
        if package_name and package_name in self.batch_pkgs:
            return 'REJECT', f"Already selected in current batch (Package: {package_name})", version
        if norm_title and norm_title in self.batch_titles:
            return 'REJECT', f"Already selected in current batch (Title: {norm_title})", version
        if url in self.batch_urls:
            return 'REJECT', f"Already selected in current batch (URL: {url})", version

        # 2. Gather all known versions across all vectors
        known_versions = set()
        if c_slug in self.registry['by_slug']:
            known_versions.update(self.registry['by_slug'][c_slug])
        if package_name and package_name in self.registry['by_pkg']:
            known_versions.update(self.registry['by_pkg'][package_name])
        if norm_title and norm_title in self.registry['by_title']:
            known_versions.update(self.registry['by_title'][norm_title])
        if url in self.registry['by_url']:
            known_versions.update(self.registry['by_url'][url])

        # 3. Fuzzy / Token Title Substring Cross-Check
        # Distinguish distinct editions (e.g. 'Spotify Lite' != 'Spotify', 'MX Player' != 'MX Player Pro')
        DISTINCT_MODIFIERS = {'lite', 'pro', 'plus', 'go', 'kids', 'music', 'tv', 'vpn', 'x', 'hd', 'mini', 'classic', 'beta'}
        cand_mods = set(norm_title.split()) & DISTINCT_MODIFIERS if norm_title else set()

        for reg_title, reg_vers in self.registry['by_title'].items():
            if norm_title and reg_title:
                reg_mods = set(reg_title.split()) & DISTINCT_MODIFIERS
                # If edition modifiers differ, do not cross-pollinate
                if cand_mods != reg_mods:
                    continue

                if norm_title == reg_title:
                    known_versions.update(reg_vers)
                elif len(norm_title) >= 6 and len(reg_title) >= 6:
                    if norm_title in reg_title or reg_title in norm_title:
                        known_versions.update(reg_vers)
                    else:
                        # Check token set exact overlap
                        t_cand_tokens = set(norm_title.split()) - DISTINCT_MODIFIERS
                        t_reg_tokens = set(reg_title.split()) - DISTINCT_MODIFIERS
                        if len(t_cand_tokens) >= 2 and (t_cand_tokens.issubset(t_reg_tokens) or t_reg_tokens.issubset(t_cand_tokens)):
                            known_versions.update(reg_vers)

        # Filter out empty or placeholder strings
        valid_kvs = {v for v in known_versions if v and v != 'latest'}

        # 4. Never posted before -> ACCEPT
        if not known_versions:
            return 'ACCEPT', 'BRAND_NEW_APP', None

        # 5. Exact match on candidate clean version -> REJECT IMMEDIATELY
        if cand_ver in known_versions or (cand_ver and cand_ver in valid_kvs):
            return 'REJECT', f"SAME_VERSION_ALREADY_POSTED (v{cand_ver})", cand_ver

        # 6. Candidate has no version but app was previously posted -> REJECT
        if not cand_ver or cand_ver == 'latest':
            prev_v = list(known_versions)[0]
            return 'REJECT', "CANDIDATE_VERSION_UNKNOWN_APP_ALREADY_POSTED", prev_v

        # 7. Compare with highest posted version
        if not valid_kvs:
            return 'ACCEPT', 'GENUINE_NEW_UPDATE_FROM_PLACEHOLDER', 'latest'

        max_kv = max(valid_kvs, key=lambda x: self.parse_version_tuple(x))
        cmp_result = self.compare_versions(cand_ver, max_kv)

        if cmp_result == 0:
            return 'REJECT', f"SEMANTIC_IDENTICAL_VERSION (v{cand_ver} == v{max_kv})", max_kv
        elif cmp_result < 0:
            return 'REJECT', f"OLDER_VERSION_THAN_POSTED (v{cand_ver} < v{max_kv})", max_kv
        else:
            return 'ACCEPT', f"GENUINE_NEW_UPDATE (v{cand_ver} > v{max_kv})", max_kv

    def lock_batch_candidate(self, candidate_item, meta):
        """Locks an approved candidate in the active batch registry."""
        url = candidate_item.get('url', '')
        title = meta.get('title') or candidate_item.get('title', '')
        version = meta.get('version') or candidate_item.get('version', 'Latest')
        package_name = meta.get('package_name', '')
        c_slug = self.get_canonical_slug(url)
        norm_title = self.normalize_title(title)
        cand_ver = self.clean_version(version)

        self.batch_slugs.add(c_slug)
        self.batch_urls.add(url)
        if package_name:
            self.batch_pkgs.add(package_name)
        if norm_title:
            self.batch_titles.add(norm_title)

        if cand_ver:
            self.registry['by_slug'].setdefault(c_slug, set()).add(cand_ver)
            if package_name:
                self.registry['by_pkg'].setdefault(package_name, set()).add(cand_ver)
            if norm_title:
                self.registry['by_title'].setdefault(norm_title, set()).add(cand_ver)
            self.registry['by_url'].setdefault(url, set()).add(cand_ver)

    def record_posted_app(self, meta, apk_info, devuploads_url):
        """Permanently saves posted APK across all registries and file stores."""
        url = meta.get('download_page', '') or apk_info.get('download_url', '')
        title = meta.get('title', '')
        version = meta.get('version', 'Latest')
        package_name = meta.get('package_name', '')
        c_slug = self.get_canonical_slug(url) if url else re.sub(r'[^a-z0-9]+', '-', self.normalize_title(title)).strip('-')
        norm_title = self.normalize_title(title)
        clean_v = self.clean_version(version)

        if url:
            self.sent_set.add(url)
            self.sent_set.add(f"{url}::{version}")
            if clean_v:
                self.sent_set.add(f"{url}::{clean_v}")
                self.sent_set.add(f"slug_id::{c_slug}::{clean_v}")
        if clean_v:
            if norm_title:
                self.sent_set.add(f"id::{norm_title}::{clean_v}")
            if package_name:
                self.sent_set.add(f"pkg_id::{package_name}::{clean_v}")

        # Save to sent_apks.json
        try:
            with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
                json.dump(sorted(list(self.sent_set)), f, indent=2)
        except Exception as e:
            print(f"[Verifier Subagent DB Save Error]: {e}")

        # Update apps_catalog.json
        if c_slug:
            self.catalog[c_slug] = {
                'slug': c_slug,
                'title': title,
                'version': version,
                'display_version': clean_v or version,
                'devuploads_url': devuploads_url,
                'size_mb': apk_info.get('size_mb', 0),
                'category': meta.get('category', 'Android Application'),
                'package_name': package_name,
                'icon_url': meta.get('image_url', ''),
                'features': meta.get('features', []),
                'description': meta.get('description', '')[:200],
                'download_page': meta.get('download_page', ''),
                'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
            }
            try:
                with open(CATALOG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.catalog, f, indent=2)
            except Exception as ce:
                print(f"[Verifier Subagent Catalog Save Error]: {ce}")
