import os, re, subprocess, tempfile, json, time, requests, shutil, base64, sqlite3
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession
from bs4 import BeautifulSoup

# ---------------- تنظیمات ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION_STRING"]

CHANNELS = ["@SOSkeyNET", "@Mrshahabx", "@vslshi"]

CONFIG_FILES = {
    "telegram": "configs.txt",
    "v2nodes_us": "us_configs.txt",
    "v2nodes_3328404": "3328404_configs.txt"
}

DB_FILE = "tested_configs.db"
TEST_URL = "http://www.gstatic.com/generate_204"
TEST_TIMEOUT = 1
MAX_TEST = 6000
BATCH_SIZE = 100

EXPIRY_HOURS = 12
MAX_RETEST = 40
MAX_FAILURES = 2
PURGE_INTERVAL = 2

# ---------------- کلاینت تلگرام ----------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

# ---------------- فیلتر بسیار سخت‌گیرانه و دقیق ----------------
def is_invalid_sni(s):
    if not s: 
        return False
    s = s.lower().strip()
    
    if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", s): 
        return True
        
    bad_domains = [
        "workers.dev", "pages.dev", "fastly.net", "ndjp.net", "ccwu.cc",
        "chickenkiller.com", "09vpn.com", "gamelistak.com", "boobie.eu.cc",
        "pink-perfect.ru", "stardevs.top", "ziqiyun.xyz", "rooster465.autos",
        "myfymain.com", "fromblancwithlove.com", "octopusss", "picassooo.info",
        "mammad.shop", "g9q.fun", "rainzone.ir", "samanehha.co", "s3-cloud.xyz",
        "ignorelist.com", "solid-dev1.online", "twilightparadox.com", "bexum.fun",
        "cgiproxy", "connectv.net", "cnae.top", "9889888.xyz", "cfvip.lol",
        "sajadi.lol", "ir"
    ]
    if any(bd in s for bd in bad_domains): 
        return True
    return False

def is_burned_reality_sni(s):
    s = s.lower().strip()
    burned = [
        "yahoo", "microsoft", "cloudflare", "sony", "apple", "icloud", 
        "amazon", "max.ru", "vk-portal", "deepl", "tradingview", "yandex",
        "mozilla", "vk.com", "speedtest", "zoom.us", "google", "ya.ru",
        "alibaba", "kinopoisk", "vk.ru", "sberbank", "ebay", "asus.com"
    ]
    if any(b in s for b in burned): 
        return True
    return False

def is_iran_friendly_config(link):
    try:
        CF_TLS_PORTS = {443, 2053, 2083, 2087, 8443, 2096}
        CF_HTTP_PORTS = {80, 8080, 8880, 2052, 2082, 2086, 2095}
        
        if link.startswith("trojan://"):
            return False
        if link.startswith("vmess://"):
            b64 = link[8:]
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            decoded = json.loads(base64.b64decode(b64).decode('utf-8'))
            port = int(decoded.get("port", 443))
            net = decoded.get("net", "tcp")
            tls = decoded.get("tls", "")
            sni = decoded.get("sni", "")
            host = decoded.get("host", "")
            
            if net == "tcp" and tls != "tls": return False
            if tls != "tls" and port not in CF_HTTP_PORTS: return False
            if tls == "tls" and port not in CF_TLS_PORTS: return False
            if is_invalid_sni(sni) or is_invalid_sni(host): return False
            return True

        elif link.startswith("ss://"):
            parsed = urlparse(link)
            port = parsed.port
            if not port: return False
            if port == 443: return False
            if port not in CF_HTTP_PORTS and port not in [8443, 2053]: return False
            return True

        elif link.startswith("vless://"):
            parsed = urlparse(link)
            port = parsed.port if parsed.port else 443
            params = parse_qs(parsed.query)
            
            security = params.get("security", [""])[0]
            fp = params.get("fp", [""])[0]
            pbk = params.get("pbk", [""])[0]
            sni = params.get("sni", [""])[0]
            host = params.get("host", [""])[0]
            
            actual_sni = sni or host or parsed.hostname
            if is_invalid_sni(actual_sni): return False
            
            if security not in ["tls", "reality"]: 
                return False
            if fp not in ["chrome", "firefox", "edge", "safari"]: 
                return False
            
            if security == "reality":
                if not pbk: return False
                if is_burned_reality_sni(actual_sni): return False
            elif security == "tls":
                if port not in CF_TLS_PORTS: return False
                
            return True
            
    except Exception:
        return False
    return False

# ---------------- توابع پایگاه داده و وضعیت ----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tested_configs
                 (config_hash TEXT PRIMARY KEY, real_delay REAL, last_test_time REAL)''')
    try:
        c.execute("ALTER TABLE tested_configs ADD COLUMN fail_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE tested_configs ADD COLUMN source TEXT DEFAULT 'telegram'")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def clean_database_with_heuristics():
    if not os.path.exists(DB_FILE): return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("SELECT config_hash FROM tested_configs")
        rows = c.fetchall()
        for row in rows:
            if not is_iran_friendly_config(row[0]):
                c.execute("DELETE FROM tested_configs WHERE config_hash=?", (row[0],))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def init_fetch_state():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS fetch_state
                 (channel TEXT PRIMARY KEY, last_msg_id INTEGER)''')
    conn.commit()
    conn.close()

def get_last_msg_id(channel):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT last_msg_id FROM fetch_state WHERE channel=?", (channel,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def set_last_msg_id(channel, msg_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO fetch_state VALUES (?, ?)", (channel, msg_id))
    conn.commit()
    conn.close()

def init_run_counter():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS run_counter
                 (id INTEGER PRIMARY KEY, counter INTEGER)''')
    c.execute("INSERT OR IGNORE INTO run_counter (id, counter) VALUES (1, 0)")
    conn.commit()
    conn.close()

def get_run_counter():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT counter FROM run_counter WHERE id=1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def set_run_counter(value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE run_counter SET counter=? WHERE id=1", (value,))
    conn.commit()
    conn.close()

def is_config_tested(config_hash):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT real_delay FROM tested_configs WHERE config_hash=?", (config_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None

def save_tested_config(config_hash, real_delay, source, fail_count=0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO tested_configs 
                 (config_hash, real_delay, last_test_time, fail_count, source) 
                 VALUES (?, ?, ?, ?, ?)''',
              (config_hash, real_delay, time.time(), fail_count, source))
    conn.commit()
    conn.close()

def delete_config(config_hash):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM tested_configs WHERE config_hash=?", (config_hash,))
    conn.commit()
    conn.close()

def increment_fail_count(config_hash):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE tested_configs SET fail_count = fail_count + 1, last_test_time = ? WHERE config_hash=?",
              (time.time(), config_hash))
    conn.commit()
    conn.close()

def get_fail_count(config_hash):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT fail_count FROM tested_configs WHERE config_hash=?", (config_hash,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def get_cached_configs(source):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT config_hash, real_delay FROM tested_configs WHERE source=? ORDER BY real_delay ASC", (source,))
    results = c.fetchall()
    conn.close()
    return results

def get_expired_configs(limit, source):
    cutoff = time.time() - EXPIRY_HOURS * 3600
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT config_hash, last_test_time, fail_count FROM tested_configs 
                 WHERE last_test_time < ? AND source=? ORDER BY last_test_time ASC LIMIT ?''',
              (cutoff, source, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# ---------------- استخراج کانفیگ‌ها (Telegram + Web Scraping) ----------------
def extract_telegram_configs():
    configs = set()
    with client:
        for channel in CHANNELS:
            last_id = get_last_msg_id(channel)
            new_messages = []
            max_id = last_id
            try:
                messages = client.iter_messages(channel, limit=200, min_id=last_id + 1, reverse=False)
                for msg in messages:
                    new_messages.append(msg)
                    if msg.id > max_id:
                        max_id = msg.id
            except Exception as e:
                print(f"⚠️ خطا در تلگرام {channel}: {e}")
                continue
            
            if new_messages:
                set_last_msg_id(channel, max_id)
            
            for msg in new_messages:
                if msg.text:
                    found = re.findall(r'(?:vless|vmess|trojan|ss)://\S+', msg.text)
                    for link in found:
                        if is_iran_friendly_config(link):
                            configs.add(link)
    return list(configs)

def extract_v2nodes_configs(base_url):
    print(f"🌐 استخراج تحت وب از: {base_url}")
    configs = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    try:
        resp = requests.get(base_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"⚠️ دریافت نشد (کد {resp.status_code})")
            return []
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        detail_links = set()
        
        # استخراج لینک‌های اختصاصی هر کانفیگ در صفحه مرجع
        for a in soup.find_all('a', href=True):
            href = a['href']
            # الگوی لینک‌های داخلی مرسوم در v2nodes
            if re.match(r'^/(?:server|node|post)s?/[\w-]+/?', href, re.IGNORECASE):
                detail_links.add("https://www.v2nodes.com" + href.lstrip('/'))
            elif href.startswith("https://www.v2nodes.com/") and re.search(r'/(?:server|node|post)s?/', href, re.IGNORECASE):
                detail_links.add(href)

        print(f"🔗 {len(detail_links)} صفحه اختصاصی یافت شد. ورود به لینک‌ها...")
        
        for link in detail_links:
            try:
                time.sleep(0.3) # جلوگیری از بلاک شدن به دلیل درخواست سریع
                detail_resp = requests.get(link, headers=headers, timeout=10)
                if detail_resp.status_code == 200:
                    # پیدا کردن لینک‌های استاندارد کانفیگ از متن خام (حذف کاراکترهای مزاحم HTML)
                    found = re.findall(r'(?:vless|vmess|trojan|ss)://[^\s<"\'\n]+', detail_resp.text)
                    for config in found:
                        if is_iran_friendly_config(config):
                            configs.add(config)
            except Exception as e:
                pass
                
    except Exception as e:
        print(f"⚠️ خطا در ارتباط: {e}")
        
    return list(configs)

# ---------------- ابزار git ----------------
def save_to_file(valid_configs, filename):
    content = "\n".join(valid_configs)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(encoded)

def git_commit_all():
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    
    files_to_add = [DB_FILE] + list(CONFIG_FILES.values())
    for f in files_to_add:
        if os.path.exists(f):
            subprocess.run(["git", "add", f], check=True)

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if result.returncode == 0:
        print("↳ بدون تغییر جدید، commit انجام نشد.")
        return

    subprocess.run(["git", "commit", "-m", "🔄 Update all configs with Real Delay test"], check=True)
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
    subprocess.run(["git", "push", "origin", "main"], check=True)
    print("↳ تغییرات با موفقیت در مخزن ثبت شد.")

# ---------------- توابع تست Xray (مشابه قبل با کمی بهینه‌سازی) ----------------
def download_xray():
    url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
    resp = requests.get(url, stream=True, timeout=30)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        for chunk in resp.iter_content(chunk_size=8192): tmp.write(chunk)
        zip_path = tmp.name
    xray_dir = tempfile.mkdtemp()
    shutil.unpack_archive(zip_path, xray_dir)
    xray_bin = os.path.join(xray_dir, "xray")
    os.chmod(xray_bin, 0o755)
    return xray_bin

def parse_link_to_outbound(link):
    # (همان ساختار تبدیل لینک به کانفیگ JSON که داشتید بدون تغییر)
    try:
        if link.startswith("vmess://"):
            b64 = link[8:]
            padded = b64 + '=' * (4 - len(b64) % 4) if len(b64) % 4 != 0 else b64
            decoded = json.loads(base64.b64decode(padded).decode('utf-8'))
            out = {
                "protocol": "vmess",
                "settings": {"vnext": [{
                    "address": decoded["add"],
                    "port": int(decoded["port"]),
                    "users": [{"id": decoded["id"], "security": decoded.get("scy", "auto")}]
                }]},
                "streamSettings": {"network": decoded.get("net", "tcp")}
            }
            if decoded.get("net") == "ws":
                out["streamSettings"]["wsSettings"] = {
                    "path": decoded.get("path", "/"),
                    "headers": {"Host": decoded.get("host", decoded["add"])} if decoded.get("host") else {}
                }
            if decoded.get("tls") == "tls":
                out["streamSettings"]["security"] = "tls"
                out["streamSettings"]["tlsSettings"] = {"serverName": decoded.get("sni", decoded["add"])}
            return out

        elif link.startswith("ss://"):
            parsed = urlparse(link)
            userinfo = parsed.username
            if userinfo:
                try:
                    padded = userinfo + '=' * (4 - len(userinfo) % 4) if len(userinfo) % 4 != 0 else userinfo
                    decoded = base64.b64decode(padded).decode('utf-8')
                    if ':' in decoded:
                        method, password = decoded.split(':', 1)
                    else:
                        method, password = "aes-256-gcm", decoded
                except:
                    if ':' in userinfo:
                        method, password = userinfo.split(':', 1)
                    else:
                        method, password = "aes-256-gcm", userinfo
            else:
                return None
            outbound = {
                "protocol": "shadowsocks",
                "settings": {"servers": [{"address": parsed.hostname, "port": int(parsed.port), "method": method, "password": password}]},
                "streamSettings": {"network": "tcp", "security": "none"}
            }
            return outbound

        elif link.startswith("vless://") or link.startswith("trojan://"):
            parsed = urlparse(link)
            if link.startswith("vless://"):
                protocol = "vless"
                settings = {"vnext": [{"address": parsed.hostname, "port": parsed.port, "users": [{"id": parsed.username, "encryption": "none", "flow": ""}]}]}
            else:
                protocol = "trojan"
                settings = {"servers": [{"address": parsed.hostname, "port": parsed.port, "password": parsed.username}]}

            params = parse_qs(parsed.query)
            def get_param(key, default=""): return params.get(key, [default])[0]

            network = get_param("type", "tcp")
            security = get_param("security", "none")
            
            if protocol == "vless" and get_param("flow", ""):
                settings["vnext"][0]["users"][0]["flow"] = get_param("flow", "")

            outbound = {
                "protocol": protocol, "settings": settings,
                "streamSettings": {"network": network, "security": security}
            }

            if network == "ws":
                outbound["streamSettings"]["wsSettings"] = {"path": get_param("path", "/"), "headers": {"Host": get_param("host", "")} if get_param("host", "") else {}}
            elif network == "tcp":
                header_type = get_param("headerType", "none")
                if header_type == "http":
                    outbound["streamSettings"]["tcpSettings"] = {"header": {"type": "http", "request": {"headers": {"Host": get_param("host", "")} if get_param("host", "") else {}, "path": get_param("path", "/")}}}
            elif network == "grpc":
                outbound["streamSettings"]["grpcSettings"] = {"serviceName": get_param("path", "/").lstrip("/"), "multiMode": False}

            if security == "tls":
                tls_settings = {"serverName": get_param("sni", parsed.hostname), "allowInsecure": get_param("allowInsecure", "0") == "1"}
                if get_param("fp", ""): tls_settings["fingerprint"] = get_param("fp", "")
                if get_param("alpn", ""): tls_settings["alpn"] = get_param("alpn", "").split(",")
                outbound["streamSettings"]["tlsSettings"] = tls_settings
            elif security == "reality":
                outbound["streamSettings"]["realitySettings"] = {
                    "serverName": get_param("sni", parsed.hostname),
                    "fingerprint": get_param("fp", "chrome"),
                    "publicKey": get_param("pbk", ""),
                    "shortId": get_param("sid", ""),
                    "spiderX": get_param("spx", "")
                }
            return outbound
    except Exception:
        return None

def test_single_config(xray_bin, link, timeout=TEST_TIMEOUT):
    outbound = parse_link_to_outbound(link)
    if not outbound: return False, 999999

    inbound = {"listen": "127.0.0.1", "port": 10808, "protocol": "socks", "settings": {"udp": False, "auth": "noauth"}}
    config = {"inbounds": [inbound], "outbounds": [outbound]}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        config_path = f.name

    xray_proc = None
    try:
        xray_proc = subprocess.Popen([xray_bin, "run", "-c", config_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)

        res = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{time_total}", "--socks5-hostname", "127.0.0.1:10808", TEST_URL, "--connect-timeout", str(timeout)], capture_output=True, text=True, timeout=timeout + 5)
        if res.returncode == 0 and res.stdout.strip():
            latency = float(res.stdout.strip()) * 1000
            if latency < timeout * 1000: return True, latency
        return False, 999999
    except Exception:
        return False, 999999
    finally:
        if xray_proc:
            xray_proc.terminate()
            try: xray_proc.wait(timeout=3)
            except: xray_proc.kill()
        try: os.unlink(config_path)
        except: pass

def process_source(source_name, raw_configs, xray_bin):
    results = {}
    total = len(raw_configs)
    
    # 1. بازیابی کانفیگ‌های مختص این سورس از کش
    cached = get_cached_configs(source_name)
    for config_hash, delay in cached:
        results[config_hash] = delay
    print(f"📊 {len(cached)} کانفیگ کش‌شده برای {source_name} یافت شد.")

    # 2. تست کانفیگ‌های جدید
    for i, link in enumerate(raw_configs, 1):
        if is_config_tested(link):
            continue
            
        short = link[:60] + "..."
        ok, delay = test_single_config(xray_bin, link)
        if ok:
            results[link] = delay
            save_tested_config(link, delay, source_name, fail_count=0)
            print(f"[{i}/{total}] ✅ {short} → {delay:.0f}ms")
        else:
            print(f"[{i}/{total}] ❌ {short} → ناموفق")

    # 3. بازبینی کانفیگ‌های قدیمیِ همین سورس
    expired = get_expired_configs(MAX_RETEST, source_name)
    if expired:
        print(f"\n🔁 بازبینی {len(expired)} کانفیگ قدیمی برای {source_name}...")
        for config_hash, last_time, fail_count in expired:
            ok, delay = test_single_config(xray_bin, config_hash)
            if ok:
                save_tested_config(config_hash, delay, source_name, fail_count=0)
                results[config_hash] = delay
            else:
                increment_fail_count(config_hash)
                if get_fail_count(config_hash) >= MAX_FAILURES:
                    delete_config(config_hash)
                    if config_hash in results: del results[config_hash]

    sorted_links = [link for link, _ in sorted(results.items(), key=lambda x: x[1])]
    return sorted_links

def perform_purge():
    print("🧹 شروع پالایش کامل کانفیگ‌های موجود...")
    xray_bin = download_xray()
    
    for source_id, filename in CONFIG_FILES.items():
        if not os.path.exists(filename): continue
        
        with open(filename, "r", encoding="utf-8") as f:
            encoded = f.read().strip()
        if not encoded: continue
            
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            links = list(set([line.strip() for line in decoded.split("\n") if line.strip()]))
        except: continue
        
        results = {}
        for link in links:
            ok, delay = test_single_config(xray_bin, link)
            if ok:
                results[link] = delay
                save_tested_config(link, delay, source_id, fail_count=0)
            else:
                delete_config(link)
                
        sorted_links = [link for link, _ in sorted(results.items(), key=lambda x: x[1])]
        save_to_file(sorted_links, filename)
        print(f"🧹 پالایش {source_id} پایان یافت. ({len(sorted_links)} معتبر)")

    git_commit_all()
    set_run_counter(0)
    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)


# ---------------- اجرای اصلی برنامه ----------------
if __name__ == "__main__":
    init_db()
    clean_database_with_heuristics()
    init_fetch_state()
    init_run_counter()

    counter = get_run_counter()
    if counter >= PURGE_INTERVAL:
        perform_purge()
        exit(0)

    print("📥 دانلود Xray-core...")
    xray_bin = download_xray()
    
    # لیست سورس‌ها و آدرس‌های مربوطه
    sources_to_scrape = [
        ("telegram", None),
        ("v2nodes_us", "https://www.v2nodes.com/country/us/"),
        ("v2nodes_3328404", "https://www.v2nodes.com/servers/3328404/")
    ]

    for source_id, url in sources_to_scrape:
        print(f"\n{'='*40}\n🚀 پردازش منبع: {source_id}\n{'='*40}")
        
        if source_id == "telegram":
            raw_configs = extract_telegram_configs()
        else:
            raw_configs = extract_v2nodes_configs(url)
            
        if not raw_configs:
            print(f"⚠️ هیچ کانفیگ سالمی از {source_id} پیدا نشد.")
            continue
            
        if len(raw_configs) > MAX_TEST:
            raw_configs = raw_configs[:MAX_TEST]
            
        valid_configs = process_source(source_id, raw_configs, xray_bin)
        
        # ذخیره در فایل مخصوص به خودش
        target_file = CONFIG_FILES[source_id]
        save_to_file(valid_configs, target_file)
        print(f"📦 فایل {target_file} با {len(valid_configs)} کانفیگ ذخیره شد.")

    # کامیت کردن تمام فایل‌های جدید یک‌جا در انتهای کار
    print("\n🔄 ثبت تغییرات در گیت...")
    git_commit_all()
    
    set_run_counter(counter + 1)
    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)
    print("✅ پردازش تمام شد.")
