import os, re, subprocess, tempfile, json, time, requests, shutil, base64, sqlite3
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession

# ---------------- تنظیمات ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION_STRING"]
CHANNEL = "@SOSkeyNET"
CONFIG_FILE = "configs.txt"
DB_FILE = "tested_configs.db"  # پایگاه داده برای ذخیره کانفیگ‌های تست‌شده
TEST_URL = "http://www.gstatic.com/generate_204"
TEST_TIMEOUT = 8  # ثانیه

# ---------------- کلاینت تلگرام ----------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

def extract_configs():
    """دریافت همه کانفیگ‌ها از کانال"""
    configs = set()
    with client:
        for msg in client.iter_messages(CHANNEL, limit=200):
            if msg.text:
                found = re.findall(r'(?:vless|vmess|trojan)://\S+', msg.text)
                for link in found:
                    configs.add(link)
    return list(configs)

# ---------------- مدیریت پایگاه داده ----------------
def init_db():
    """ایجاد جدول در صورت عدم وجود"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tested_configs
                 (config_hash TEXT PRIMARY KEY, real_delay REAL, last_test_time REAL)''')
    conn.commit()
    conn.close()

def is_config_tested(config_hash):
    """بررسی اینکه آیا کانفیگ قبلاً تست شده است"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT real_delay FROM tested_configs WHERE config_hash=?", (config_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None

def save_tested_config(config_hash, real_delay):
    """ذخیره کانفیگ تست‌شده در پایگاه داده"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO tested_configs VALUES (?, ?, ?)",
              (config_hash, real_delay, time.time()))
    conn.commit()
    conn.close()

def get_cached_configs():
    """بازیابی کانفیگ‌های قبلاً تست‌شده و مرتب‌سازی بر اساس Real Delay"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT config_hash, real_delay FROM tested_configs ORDER BY real_delay ASC")
    results = c.fetchall()
    conn.close()
    return results

# ---------------- دانلود Xray-core ----------------
def download_xray():
    url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
    resp = requests.get(url, stream=True, timeout=30)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        zip_path = tmp.name
    xray_dir = tempfile.mkdtemp()
    shutil.unpack_archive(zip_path, xray_dir)
    xray_bin = os.path.join(xray_dir, "xray")
    os.chmod(xray_bin, 0o755)
    return xray_bin

# ---------------- تبدیل لینک به Outbound Xray ----------------
def parse_link_to_outbound(link):
    """تبدیل لینک vless/vmess/trojan به outbound کانفیگ Xray"""
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

        elif link.startswith("vless://") or link.startswith("trojan://"):
            parsed = urlparse(link)
            if link.startswith("vless://"):
                uuid = parsed.username
                protocol = "vless"
                settings = {"vnext": [{
                    "address": parsed.hostname,
                    "port": parsed.port,
                    "users": [{"id": uuid, "encryption": "none", "flow": ""}]
                }]}
            else:
                password = parsed.username
                protocol = "trojan"
                settings = {"servers": [{
                    "address": parsed.hostname,
                    "port": parsed.port,
                    "password": password
                }]}

            params = parse_qs(parsed.query)
            def get_param(key, default=""):
                return params.get(key, [default])[0]

            network = get_param("type", "tcp")
            security = get_param("security", "none")
            sni = get_param("sni", parsed.hostname)
            host = get_param("host", "")
            path = get_param("path", "/")
            header_type = get_param("headerType", "none")
            alpn = get_param("alpn", "")
            fp = get_param("fp", "")
            flow = get_param("flow", "")
            if protocol == "vless" and flow:
                settings["vnext"][0]["users"][0]["flow"] = flow

            outbound = {
                "protocol": protocol,
                "settings": settings,
                "streamSettings": {
                    "network": network,
                    "security": security
                }
            }

            if network == "ws":
                outbound["streamSettings"]["wsSettings"] = {
                    "path": path,
                    "headers": {"Host": host} if host else {}
                }
            elif network == "tcp":
                if header_type == "http":
                    outbound["streamSettings"]["tcpSettings"] = {
                        "header": {
                            "type": "http",
                            "request": {
                                "headers": {"Host": host} if host else {},
                                "path": path if path != "/" else "/"
                            }
                        }
                    }
                elif header_type and header_type != "none":
                    outbound["streamSettings"]["tcpSettings"] = {"header": {"type": header_type}}
            elif network == "grpc":
                outbound["streamSettings"]["grpcSettings"] = {
                    "serviceName": path.lstrip("/"),
                    "multiMode": False
                }
            elif network == "xhttp":
                outbound["streamSettings"]["xhttpSettings"] = {
                    "mode": get_param("mode", "auto"),
                    "path": path,
                    "host": host
                }

            if security == "tls":
                tls_settings = {"serverName": sni, "allowInsecure": get_param("allowInsecure", "0") == "1"}
                if alpn:
                    tls_settings["alpn"] = alpn.split(",")
                if fp:
                    tls_settings["fingerprint"] = fp
                outbound["streamSettings"]["tlsSettings"] = tls_settings
            elif security == "reality":
                outbound["streamSettings"]["realitySettings"] = {
                    "serverName": sni,
                    "fingerprint": fp if fp else "chrome",
                    "publicKey": get_param("pbk", ""),
                    "shortId": get_param("sid", ""),
                    "spiderX": get_param("spx", "")
                }

            return outbound

    except Exception:
        return None

def test_single_config(xray_bin, link, timeout=TEST_TIMEOUT):
    """تست واقعی با Xray-core و برگرداندن Real Delay (میلی‌ثانیه)"""
    outbound = parse_link_to_outbound(link)
    if not outbound:
        return False, 999999

    inbound = {
        "listen": "127.0.0.1", "port": 10808, "protocol": "socks",
        "settings": {"udp": False, "auth": "noauth"}
    }
    config = {"inbounds": [inbound], "outbounds": [outbound]}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        config_path = f.name

    xray_proc = None
    try:
        xray_proc = subprocess.Popen(
            [xray_bin, "run", "-c", config_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2.5)

        res = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_total}",
             "--socks5-hostname", "127.0.0.1:10808", TEST_URL,
             "--connect-timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 5
        )

        if res.returncode == 0 and res.stdout.strip():
            latency = float(res.stdout.strip()) * 1000  # ms
            if latency < timeout * 1000:
                return True, latency
        return False, 999999

    except Exception:
        return False, 999999
    finally:
        if xray_proc:
            xray_proc.terminate()
            try:
                xray_proc.wait(timeout=3)
            except:
                xray_proc.kill()
        try:
            os.unlink(config_path)
        except:
            pass

def test_all(configs):
    """تست کانفیگ‌های جدید و ادغام با نتایج قبلی"""
    print("📥 دانلود Xray-core...")
    xray_bin = download_xray()
    print("✅ Xray-core آماده شد.\n")

    results = {}
    total = len(configs)
    new_tested = 0
    skipped = 0

    # بازیابی نتایج قبلی از پایگاه داده
    cached = get_cached_configs()
    for config_hash, delay in cached:
        results[config_hash] = delay
    print(f"📊 {len(cached)} کانفیگ قبلاً تست شده و از پایگاه داده بازیابی شد.\n")

    for i, link in enumerate(configs, 1):
        short = link[:70] + ("..." if len(link) > 70 else "")
        
        # بررسی اینکه آیا کانفیگ قبلاً تست شده است
        config_hash = link  # استفاده از خود لینک به عنوان شناسه یکتا
        
        if is_config_tested(config_hash):
            print(f"[{i}/{total}] ⏭️ {short} → قبلاً تست شده، رد می‌شود")
            skipped += 1
            continue
        
        ok, delay = test_single_config(xray_bin, link)
        
        if ok:
            results[config_hash] = delay
            save_tested_config(config_hash, delay)
            new_tested += 1
            print(f"[{i}/{total}] ✅ {short} → Real Delay: {delay:.0f}ms (جدید)")
        else:
            print(f"[{i}/{total}] ❌ {short} → ناموفق")

    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)
    
    # مرتب‌سازی بر اساس Real Delay (کمترین)
    sorted_results = sorted(results.items(), key=lambda x: x[1])
    
    print(f"\n📈 خلاصه: {len(sorted_results)} کانفیگ معتبر (⏭️ {skipped} قبلاً تست شده, ✅ {new_tested} جدید)")
    return [link for link, _ in sorted_results]

if __name__ == "__main__":
    # راه‌اندازی پایگاه داده
    init_db()
    
    print("📡 دریافت کانفیگ‌ها از تلگرام...")
    raw = extract_configs()
    print(f"📋 {len(raw)} کانفیگ پیدا شد.\n")

    if not raw:
        print("⚠️ هیچ کانفیگی پیدا نشد!")
        exit(1)

    valid = test_all(raw)

    # ساخت Subscription (Base64)
    content = "\n".join(valid)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)

    print(f"\n🎉 تمام! {len(valid)} کانفیگ معتبر بر اساس Real Delay مرتب و در {CONFIG_FILE} ذخیره شد.")
