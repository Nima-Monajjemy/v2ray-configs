import os, re, subprocess, tempfile, json, time, requests, shutil, base64
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession

# ---------------- تنظیمات ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION_STRING"]
CHANNEL = "@SOSkeyNET"               # ← کانال شما
CONFIG_FILE = "configs.txt"
TEST_URL = "http://www.gstatic.com/generate_204"

# ---------------- دریافت کانفیگ‌ها از تلگرام ----------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

def extract_configs():
    configs = set()
    with client:
        for msg in client.iter_messages(CHANNEL, limit=100):
            if msg.text:
                # همه‌ی لینک‌های vless, vmess, trojan را استخراج کن
                found = re.findall(r'(vless|vmess|trojan)://[^\s]+', msg.text)
                for c in found:
                    configs.add(c)
    return list(configs)

# ---------------- دانلود Xray-core ----------------
def download_xray():
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

# ---------------- ساخت کانفیگ تست Xray از یک لینک ----------------
def parse_link_to_outbound(link):
    """تبدیل یک لینک vless/vmess/trojan به outbound object برای Xray"""
    try:
        if link.startswith("vmess://"):
            b64 = link[8:]
            # اصلاح padding base64
            padded = b64 + '=' * (4 - len(b64) % 4) if len(b64) % 4 != 0 else b64
            decoded = json.loads(subprocess.check_output(
                ["bash", "-c", f"echo {padded} | base64 -d"], text=True))
            out = {
                "protocol": "vmess",
                "settings": {"vnext": [{
                    "address": decoded["add"],
                    "port": int(decoded["port"]),
                    "users": [{"id": decoded["id"], "security": decoded.get("scy", "auto")}]
                }]},
                "streamSettings": {"network": decoded.get("net", "tcp")}
            }
            # تنظیمات ویژه برای ws
            if decoded.get("net") == "ws":
                out["streamSettings"]["wsSettings"] = {
                    "path": decoded.get("path", "/"),
                    "headers": {"Host": decoded.get("host", decoded["add"])} if decoded.get("host") else {}
                }
            # TLS
            if decoded.get("tls") == "tls":
                out["streamSettings"]["security"] = "tls"
                out["streamSettings"]["tlsSettings"] = {
                    "serverName": decoded.get("sni", decoded["add"])
                }
            return out

        elif link.startswith("vless://"):
            parsed = urlparse(link)
            uuid = parsed.username  # vless://uuid@...
            address = parsed.hostname
            port = parsed.port
            params = parse_qs(parsed.query)
            # تابع کمکی برای دریافت یک مقدار از query
            def get_param(key, default=""):
                return params.get(key, [default])[0]

            network = get_param("type", "tcp")
            security = get_param("security", "none")
            flow = get_param("flow", "")
            sni = get_param("sni", address)
            host = get_param("host", "")
            path = get_param("path", "/")
            header_type = get_param("headerType", "none")
            alpn = get_param("alpn", "")
            fp = get_param("fp", "")

            outbound = {
                "protocol": "vless",
                "settings": {"vnext": [{
                    "address": address,
                    "port": int(port),
                    "users": [{"id": uuid, "encryption": "none", "flow": flow}]
                }]},
                "streamSettings": {
                    "network": network,
                    "security": security
                }
            }

            # تنظیمات شبکه
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
                    outbound["streamSettings"]["tcpSettings"] = {
                        "header": {"type": header_type}
                    }
            elif network == "grpc":
                outbound["streamSettings"]["grpcSettings"] = {
                    "serviceName": path.lstrip("/"),
                    "multiMode": False
                }
            elif network == "xhttp":
                # XHTTP (برای xray-core نسخه‌های جدید)
                outbound["streamSettings"]["xhttpSettings"] = {
                    "mode": get_param("mode", "auto"),
                    "path": path,
                    "host": host,
                    "extra": {}
                }

            # TLS
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

        elif link.startswith("trojan://"):
            parsed = urlparse(link)
            password = parsed.username  # trojan://password@...
            address = parsed.hostname
            port = parsed.port
            params = parse_qs(parsed.query)
            def get_param(key, default=""):
                return params.get(key, [default])[0]

            network = get_param("type", "tcp")
            security = get_param("security", "none")
            sni = get_param("sni", address)
            host = get_param("host", "")
            path = get_param("path", "/")
            alpn = get_param("alpn", "")
            fp = get_param("fp", "")
            header_type = get_param("headerType", "none")

            outbound = {
                "protocol": "trojan",
                "settings": {"servers": [{
                    "address": address,
                    "port": int(port),
                    "password": password
                }]},
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
            elif network == "tcp" and header_type == "http":
                outbound["streamSettings"]["tcpSettings"] = {
                    "header": {
                        "type": "http",
                        "request": {
                            "headers": {"Host": host} if host else {},
                            "path": path if path != "/" else "/"
                        }
                    }
                }
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
                tls_settings = {
                    "serverName": sni,
                    "allowInsecure": get_param("allowInsecure", "0") == "1"
                }
                if alpn:
                    tls_settings["alpn"] = alpn.split(",")
                if fp:
                    tls_settings["fingerprint"] = fp
                outbound["streamSettings"]["tlsSettings"] = tls_settings

            return outbound

    except Exception as e:
        # خطا در تجزیه → صرف نظر از این لینک
        return None

# ---------------- تست یک کانفیگ با اجرای Xray + curl ----------------
def test_single_config(xray_bin, link, timeout=8):
    outbound = parse_link_to_outbound(link)
    if not outbound:
        return False, 9999

    inbound = {
        "listen": "127.0.0.1", "port": 10808, "protocol": "socks",
        "settings": {"udp": False, "auth": "noauth"}
    }
    config = {"inbounds": [inbound], "outbounds": [outbound]}
    config_json = json.dumps(config)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(config_json)
        config_path = f.name

    xray_proc = subprocess.Popen(
        [xray_bin, "run", "-c", config_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        time.sleep(2)
        res = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_total}",
             "--socks5-hostname", "127.0.0.1:10808", TEST_URL,
             "--connect-timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout+3
        )
        latency = float(res.stdout.strip()) if res.stdout.strip() and res.returncode == 0 else 9999
        success = (res.returncode == 0 and latency < timeout)
        return success, latency * 1000
    except:
        return False, 9999
    finally:
        xray_proc.terminate()
        try:
            xray_proc.wait(timeout=3)
        except:
            xray_proc.kill()
        os.unlink(config_path)

# ---------------- تست همه‌ی کانفیگ‌ها ----------------
def test_all(configs):
    xray_bin = download_xray()
    results = []
    for link in configs:
        ok, ping = test_single_config(xray_bin, link)
        if ok:
            results.append((ping, link))
            print(f"✅ {link[:60]}... پینگ: {ping:.0f}ms")
        else:
            print(f"❌ {link[:60]}... ناموفق")
    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)
    results.sort(key=lambda x: x[0])
    return [link for _, link in results]

# ---------------- اجرای اصلی ----------------
if __name__ == "__main__":
    print("دریافت کانفیگ‌ها از تلگرام...")
    raw = extract_configs()
    print(f"{len(raw)} کانفیگ یافت شد. شروع تست...")
    valid = test_all(raw)

    # ترکیب کانفیگ‌ها و کدگذاری base64 برای Subscription
    content = "\n".join(valid)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)
    print(f"پایان. {len(valid)} کانفیگ معتبر در {CONFIG_FILE} ذخیره شد.")
