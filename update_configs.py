import os, re, subprocess, tempfile, json, time, requests, shutil, base64
from telethon import TelegramClient
from telethon.sessions import StringSession

# ---------------- تنظیمات ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION_STRING"]
CHANNEL = "@SOSkeyNET"
CONFIG_FILE = "configs.txt"
TEST_URL = "http://www.gstatic.com/generate_204"
TEST_TIMEOUT = 8  # حداکثر زمان تست هر کانفیگ (ثانیه)

# ---------------- کلاینت تلگرام ----------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

def extract_configs():
    """دریافت همه کانفیگ‌ها از کانال تلگرام"""
    configs = set()
    with client:
        for msg in client.iter_messages(CHANNEL, limit=200):
            if msg.text:
                found = re.findall(r'(vless|vmess|trojan)://[^\s]+', msg.text)
                for c in found:
                    configs.add(c)
    return list(configs)

# ---------------- دانلود Xray-core ----------------
def download_xray():
    """دانلود Xray-core مخصوص لینوکس"""
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

# ---------------- تبدیل لینک به Outbound کانفیگ Xray ----------------
def parse_link_to_outbound(link):
    """
    تبدیل یه لینک vless://, vmess:// یا trojan:// 
    به یه outbound object کامل برای Xray-core
    """
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
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(link)
            
            if link.startswith("vless://"):
                uuid = parsed.username
                protocol = "vless"
                settings = {"vnext": [{
                    "address": parsed.hostname,
                    "port": parsed.port,
                    "users": [{"id": uuid, "encryption": "none", "flow": ""}]
                }]}
            else:  # trojan
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
            
            # آپدیت flow برای vless
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

            # تنظیمات transport
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

            # تنظیمات TLS
            if security == "tls":
                tls_settings = {
                    "serverName": sni,
                    "allowInsecure": get_param("allowInsecure", "0") == "1"
                }
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
    """
    تست یه کانفیگ با Xray-core واقعی
    یه پروکسی SOCKS5 روی پورت 10808 ایجاد می‌کنه و Real Delay رو اندازه می‌گیره
    برمی‌گردونه: (موفقیت, تاخیر به میلی‌ثانیه)
    """
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
        time.sleep(2.5)  # صبر برای راه‌اندازی کامل Xray

        start = time.time()
        res = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_total}",
             "--socks5-hostname", "127.0.0.1:10808", TEST_URL,
             "--connect-timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 5
        )
        elapsed = time.time() - start

        if res.returncode == 0 and res.stdout.strip():
            latency = float(res.stdout.strip()) * 1000  # تبدیل ثانیه به میلی‌ثانیه
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
    """تست همه کانفیگ‌ها و برگردوندن لیست مرتب شده بر اساس Real Delay"""
    print("📥 دانلود Xray-core...")
    xray_bin = download_xray()
    print("✅ Xray-core آماده شد.\n")

    results = []
    total = len(configs)
    
    for i, link in enumerate(configs, 1):
        short = link[:70] + ("..." if len(link) > 70 else "")
        ok, delay = test_single_config(xray_bin, link)
        
        if ok:
            results.append((delay, link))
            print(f"[{i}/{total}] ✅ {short} → Real Delay: {delay:.0f}ms")
        else:
            print(f"[{i}/{total}] ❌ {short} → ناموفق")

    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)
    
    # مرتب‌سازی بر اساس کمترین تاخیر (Real Delay)
    results.sort(key=lambda x: x[0])
    return [link for _, link in results]


if __name__ == "__main__":
    print("📡 دریافت کانفیگ‌ها از تلگرام...")
    raw = extract_configs()
    print(f"📋 {len(raw)} کانفیگ پیدا شد.\n")
    
    if not raw:
        print("⚠️ هیچ کانفیگی پیدا نشد!")
        exit(1)
    
    valid = test_all(raw)
    
    # ساخت فایل نهایی با فرمت Base64 (مناسب Subscription)
    content = "\n".join(valid)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)
    
    print(f"\n🎉 تمام! {len(valid)} کانفیگ معتبر بر اساس Real Delay مرتب و در {CONFIG_FILE} ذخیره شد.")
