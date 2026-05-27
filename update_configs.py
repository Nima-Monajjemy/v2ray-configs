import os, re, subprocess, tempfile, json, time, requests, shutil, base64
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession

# ---------------- تنظیمات ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION_STRING"]
CHANNEL = "@SOSkeyNET"          # ← نام کانال شما
CONFIG_FILE = "configs.txt"
TEST_URL = "http://www.gstatic.com/generate_204" # آدرس تست (ساده و سبک)
LOCAL_PROXY_PORT = 10808
XRAY_TIMEOUT = 10 # حداکثر زمان انتظار برای هر تست (ثانیه)

# ---------------- ۱. دریافت کانفیگ‌ها از تلگرام ----------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

def extract_configs():
    configs = set()
    with client:
        for msg in client.iter_messages(CHANNEL, limit=100):
            if msg.text:
                found = re.findall(r'(vless|vmess|trojan)://[^\s]+', msg.text)
                for c in found:
                    configs.add(c)
    return list(configs)

# ---------------- ۲. تست با Xray-core و اندازه‌گیری Real Delay ----------------
def download_xray():
    """دانلود Xray-core مخصوص لینوکس (محیط گیت‌هاب)"""
    url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
    resp = requests.get(url, stream=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        zip_path = tmp.name
    xray_dir = tempfile.mkdtemp()
    shutil.unpack_archive(zip_path, xray_dir)
    xray_bin = os.path.join(xray_dir, "xray")
    os.chmod(xray_bin, 0o755)
    return xray_bin

def make_test_config(outbound_link):
    """تبدیل یک لینک به کانفیگ کامل Xray برای تست"""
    inbound = {
        "listen": "127.0.0.1", "port": LOCAL_PROXY_PORT, "protocol": "socks",
        "settings": {"udp": False, "auth": "noauth"}
    }
    outbound = {}
    if outbound_link.startswith("vmess://"):
        try:
            b64 = outbound_link[8:]
            padded = b64 + '=' * (4 - len(b64) % 4) if len(b64) % 4 != 0 else b64
            decoded = json.loads(subprocess.check_output(["bash", "-c", f"echo {padded} | base64 -d"], text=True))
            outbound = {
                "protocol": "vmess",
                "settings": {"vnext": [{
                    "address": decoded["add"],
                    "port": int(decoded["port"]),
                    "users": [{"id": decoded["id"], "security": decoded.get("scy", "auto")}]
                }]},
                "streamSettings": {"network": decoded.get("net", "tcp")}
            }
            if decoded.get("net") == "ws":
                outbound["streamSettings"]["wsSettings"] = {
                    "path": decoded.get("path", "/"),
                    "headers": {"Host": decoded.get("host", decoded["add"])} if decoded.get("host") else {}
                }
            if decoded.get("tls") == "tls":
                outbound["streamSettings"]["security"] = "tls"
                outbound["streamSettings"]["tlsSettings"] = {"serverName": decoded.get("sni", decoded["add"])}
        except:
            return None
    elif outbound_link.startswith("vless://") or outbound_link.startswith("trojan://"):
        try:
            parsed = urlparse(outbound_link)
            uuid_or_pass = parsed.username
            address = parsed.hostname
            port = parsed.port
            params = parse_qs(parsed.query)
            def get_param(key, default=""):
                return params.get(key, [default])[0]

            protocol = "vless" if outbound_link.startswith("vless://") else "trojan"
            network = get_param("type", "tcp")
            security = get_param("security", "none")
            sni = get_param("sni", address)
            host = get_param("host", "")
            path = get_param("path", "/")
            header_type = get_param("headerType", "none")

            if protocol == "vless":
                outbound = {
                    "protocol": "vless",
                    "settings": {"vnext": [{
                        "address": address, "port": int(port),
                        "users": [{"id": uuid_or_pass, "encryption": "none", "flow": get_param("flow", "")}]
                    }]}
                }
            else: # trojan
                outbound = {
                    "protocol": "trojan",
                    "settings": {"servers": [{"address": address, "port": int(port), "password": uuid_or_pass}]}
                }

            outbound["streamSettings"] = {"network": network, "security": security}
            if network == "ws":
                outbound["streamSettings"]["wsSettings"] = {"path": path, "headers": {"Host": host} if host else {}}
            elif network == "tcp" and header_type == "http":
                outbound["streamSettings"]["tcpSettings"] = {
                    "header": {"type": "http", "request": {"headers": {"Host": host} if host else {}, "path": path}}
                }
            if security == "tls":
                outbound["streamSettings"]["tlsSettings"] = {
                    "serverName": sni,
                    "allowInsecure": get_param("allowInsecure", "0") == "1"
                }
        except:
            return None
    else:
        return None

    return json.dumps({"inbounds": [inbound], "outbounds": [outbound]})

def test_with_real_delay(xray_bin, config_json, timeout=XRAY_TIMEOUT):
    """تست با Xray و اندازه‌گیری Real Delay با requests"""
    if not config_json:
        return False, 9999

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(config_json)
        config_path = f.name

    xray_proc = subprocess.Popen(
        [xray_bin, "run", "-c", config_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        time.sleep(2) # صبر برای راه‌اندازی پروکسی
        proxy_url = f"socks5h://127.0.0.1:{LOCAL_PROXY_PORT}"
        start = time.time()
        response = requests.get(TEST_URL, proxies={"http": proxy_url, "https": proxy_url}, timeout=timeout)
        end = time.time()
        if response.status_code in [200, 204, 301, 302]:
            real_delay = (end - start) * 1000
            return True, real_delay
        else:
            return False, 9999
    except Exception:
        return False, 9999
    finally:
        xray_proc.terminate()
        try:
            xray_proc.wait(timeout=3)
        except:
            xray_proc.kill()
        os.unlink(config_path)

def test_all(configs):
    xray_bin = download_xray()
    results = []
    print(f"شروع تست Real Delay روی {len(configs)} کانفیگ با Xray-core...")
    for link in configs:
        conf = make_test_config(link)
        ok, delay = test_with_real_delay(xray_bin, conf)
        if ok:
            results.append((delay, link))
            print(f"✅ {link[:60]}... Real Delay: {delay:.0f}ms")
        else:
            print(f"❌ {link[:60]}... ناموفق")
    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)
    results.sort(key=lambda x: x[0])
    return [link for _, link in results]

# ---------------- اجرای اصلی ----------------
if __name__ == "__main__":
    print("دریافت کانفیگ‌ها از تلگرام...")
    raw_configs = extract_configs()
    print(f"{len(raw_configs)} کانفیگ یافت شد.")
    valid = test_all(raw_configs)
    content = "\n".join(valid)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)
    print(f"پایان. {len(valid)} کانفیگ معتبر با Real Delay در {CONFIG_FILE} ذخیره شد.")
