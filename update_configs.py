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
DB_FILE = "tested_configs.db"
TEST_URL = "http://www.gstatic.com/generate_204"
TEST_TIMEOUT = 4                      # ثانیه
MAX_TEST = 2000                        # حداکثر تعداد کانفیگی که تست می‌شود
BATCH_SIZE = 100                      # پس از هر BATCH_SIZE کانفیگ، فایل نهایی به‌روز می‌شود

# --------------- تنظیمات جدید برای حذف کانفیگ‌های خراب ---------------
EXPIRY_HOURS = 12                     # کانفیگ‌هایی که بیشتر از این مدت از تستشان گذشته، واجد شرایط تست مجدد
MAX_RETEST = 20                       # حداکثر تعداد کانفیگ قدیمی که در هر اجرا دوباره تست می‌شوند
MAX_FAILURES = 2                      # تعداد شکست‌های متوالی مجاز قبل از حذف کامل

# ---------------- کلاینت تلگرام ----------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

def extract_configs():
    configs = set()
    with client:
        for msg in client.iter_messages(CHANNEL, limit=200):
            if msg.text:
                found = re.findall(r'(?:vless|vmess|trojan)://\S+', msg.text)
                for link in found:
                    configs.add(link)
    return list(configs)

# ---------------- مدیریت پایگاه داده (با ستون جدید fail_count) ----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tested_configs
                 (config_hash TEXT PRIMARY KEY, real_delay REAL, last_test_time REAL)''')
    # افزودن ستون fail_count اگر وجود نداشته باشد
    try:
        c.execute("ALTER TABLE tested_configs ADD COLUMN fail_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # ستون از قبل وجود دارد
    conn.commit()
    conn.close()

def is_config_tested(config_hash):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT real_delay FROM tested_configs WHERE config_hash=?", (config_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None

def save_tested_config(config_hash, real_delay, fail_count=0):
    """ذخیره یا به‌روزرسانی یک کانفیگ موفق (fail_count صفر می‌شود)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO tested_configs VALUES (?, ?, ?, ?)",
              (config_hash, real_delay, time.time(), fail_count))
    conn.commit()
    conn.close()

def delete_config(config_hash):
    """حذف کامل یک کانفیگ از پایگاه داده"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM tested_configs WHERE config_hash=?", (config_hash,))
    conn.commit()
    conn.close()

def increment_fail_count(config_hash):
    """افزایش fail_count و به‌روزرسانی زمان آخرین تست (برای شکست)"""
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

def get_cached_configs():
    """بازیابی همه کانفیگ‌های معتبر (بدون در نظر گرفتن fail_count، چون فقط موفق‌ها ذخیره می‌شوند)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT config_hash, real_delay FROM tested_configs ORDER BY real_delay ASC")
    results = c.fetchall()
    conn.close()
    return results

def get_expired_configs(limit):
    """بازیابی کانفیگ‌هایی که زمان آخرین تست آن‌ها از EXPIRY_HOURS گذشته است (قدیمی‌ترین‌ها اولویت دارند)"""
    cutoff = time.time() - EXPIRY_HOURS * 3600
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT config_hash, last_test_time, fail_count FROM tested_configs WHERE last_test_time < ? ORDER BY last_test_time ASC LIMIT ?",
              (cutoff, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# ---------------- ابزار git برای commit و push ----------------
def setup_git():
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)

def commit_and_push(valid_configs, new_count, total_valid):
    """نوشتن فایل، commit و push کردن تغییرات"""
    content = "\n".join(valid_configs)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)

    subprocess.run(["git", "add", CONFIG_FILE, DB_FILE], check=True)

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if result.returncode == 0:
        print("   ↳ بدون تغییر جدید، commit انجام نشد.")
        return

    commit_msg = f"🔄 Batch update: +{new_count} new configs (total valid: {total_valid})"
    subprocess.run(["git", "commit", "-m", commit_msg], check=True)
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
    subprocess.run(["git", "push", "origin", "main"], check=True)
    print(f"   ↳ تغییرات با موفقیت push شد ({total_valid} کانفیگ معتبر).")

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

# ---------------- تبدیل لینک به Outbound Xray (بدون تغییر) ----------------
def parse_link_to_outbound(link):
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
            latency = float(res.stdout.strip()) * 1000
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

def test_all_with_incremental_save(configs):
    print("📥 دانلود Xray-core...")
    xray_bin = download_xray()
    print("✅ Xray-core آماده شد.\n")

    results = {}           # نگهداری همه کانفیگ‌های معتبر (link -> delay)
    new_in_batch = 0
    total_processed = 0
    total = len(configs)

    # بازیابی نتایج قبلی از پایگاه داده
    cached = get_cached_configs()
    for config_hash, delay in cached:
        results[config_hash] = delay
    print(f"📊 {len(cached)} کانفیگ قبلاً تست شده و از پایگاه داده بازیابی شد.\n")

    # ------------------ تست کانفیگ‌های جدید ------------------
    for i, link in enumerate(configs, 1):
        total_processed += 1
        short = link[:70] + ("..." if len(link) > 70 else "")

        if is_config_tested(link):
            print(f"[{i}/{total}] ⏭️ {short} → قبلاً تست شده، رد می‌شود")
            continue

        ok, delay = test_single_config(xray_bin, link)

        if ok:
            results[link] = delay
            save_tested_config(link, delay, fail_count=0)   # موفق → fail_count صفر
            new_in_batch += 1
            print(f"[{i}/{total}] ✅ {short} → Real Delay: {delay:.0f}ms (جدید)")
        else:
            # کانفیگ جدید شکست خورد → وارد دیتابیس نمی‌شود (در اجراهای بعدی دوباره شانس تست دارد)
            print(f"[{i}/{total}] ❌ {short} → ناموفق")

        # بررسی پایان دسته (batch)
        if total_processed % BATCH_SIZE == 0 or i == total:
            if new_in_batch > 0:
                sorted_links = [link for link, _ in sorted(results.items(), key=lambda x: x[1])]
                total_valid = len(sorted_links)
                print(f"\n📦 پایان دسته {total_processed}/{total} | +{new_in_batch} کانفیگ جدید معتبر (کل: {total_valid})")
                commit_and_push(sorted_links, new_in_batch, total_valid)
                new_in_batch = 0
            else:
                print(f"\n📦 پایان دسته {total_processed}/{total} | بدون کانفیگ جدید معتبر در این دسته")

    # ------------------ بازبینی کانفیگ‌های قدیمی منقضی شده ------------------
    print("\n🔁 شروع بازبینی کانفیگ‌های قدیمی (منقضی شده)...")
    expired = get_expired_configs(MAX_RETEST)
    if not expired:
        print("✅ هیچ کانفیگ منقضی شده‌ای یافت نشد.")
    else:
        recheck_changes = False
        for config_hash, last_time, fail_count in expired:
            short = config_hash[:70] + ("..." if len(config_hash) > 70 else "")
            ok, delay = test_single_config(xray_bin, config_hash)

            if ok:
                # تست موفق → بازنشانی fail_count و به‌روزرسانی delay و زمان
                save_tested_config(config_hash, delay, fail_count=0)
                results[config_hash] = delay
                print(f"🔁 ✅ {short} → دوباره سالم شد (Real Delay: {delay:.0f}ms)")
                recheck_changes = True
            else:
                # تست ناموفق → افزایش fail_count
                increment_fail_count(config_hash)
                new_fail_count = get_fail_count(config_hash)
                print(f"🔁 ❌ {short} → همچنان خراب (شکست {new_fail_count} از {MAX_FAILURES})")
                if new_fail_count >= MAX_FAILURES:
                    # حذف کامل از پایگاه داده و از لیست results
                    delete_config(config_hash)
                    if config_hash in results:
                        del results[config_hash]
                    print(f"   🗑️ حذف شد (بیش از {MAX_FAILURES} شکست متوالی)")
                    recheck_changes = True
                # اگر fail_count به آستانه نرسیده باشد، در دیتابیس می‌ماند تا اجرای بعدی دوباره بررسی شود

        if recheck_changes:
            sorted_links = [link for link, _ in sorted(results.items(), key=lambda x: x[1])]
            total_valid = len(sorted_links)
            print(f"\n📦 پایان بازبینی | کل کانفیگ‌های معتبر: {total_valid}")
            commit_and_push(sorted_links, 0, total_valid)   # new_count=0 زیرا کانفیگ جدیدی اضافه نشده، فقط وضعیت‌ها تغییر کرده
        else:
            print("📦 پایان بازبینی | تغییری در لیست نهایی ایجاد نشد.")

    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)
    print(f"\n🔚 تست تمام شد. مجموع کانفیگ‌های معتبر: {len(results)}")
    return [link for link, _ in sorted(results.items(), key=lambda x: x[1])]

if __name__ == "__main__":
    init_db()
    setup_git()

    print("📡 دریافت کانفیگ‌ها از تلگرام...")
    raw = extract_configs()
    print(f"📋 {len(raw)} کانفیگ پیدا شد.\n")

    if not raw:
        print("⚠️ هیچ کانفیگی پیدا نشد!")
        exit(1)

    if len(raw) > MAX_TEST:
        print(f"⚠️ محدودیت تست: فقط {MAX_TEST} کانفیگ اول تست می‌شود.\n")
        raw = raw[:MAX_TEST]

    valid = test_all_with_incremental_save(raw)

    # ذخیره‌سازی نهایی (در صورت عدم commit در مراحل قبلی)
    content = "\n".join(valid)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)

    print(f"\n🎉 تمام! {len(valid)} کانفیگ معتبر بر اساس Real Delay مرتب و در {CONFIG_FILE} ذخیره شد.")
