import os, re, socket, time, base64
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession

# ---------------- تنظیمات ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION_STRING"]
CHANNEL = "@SOSkeyNET"
CONFIG_FILE = "configs.txt"

# ---------------- استخراج آدرس و پورت از لینک ----------------
def get_address_port(link):
    """آدرس و پورت را از لینک vless/vmess/trojan برمی‌گرداند"""
    try:
        if link.startswith("vmess://"):
            b64 = link[8:]
            # اصلاح padding
            padded = b64 + '=' * (4 - len(b64) % 4) if len(b64) % 4 != 0 else b64
            decoded = base64.b64decode(padded)
            import json
            info = json.loads(decoded)
            return info["add"], int(info["port"])
        elif link.startswith("vless://") or link.startswith("trojan://"):
            parsed = urlparse(link)
            return parsed.hostname, parsed.port
        else:
            return None, None
    except:
        return None, None

# ---------------- تست اتصال TCP ساده ----------------
def tcp_test(address, port, timeout=5):
    """اگر ظرف timeout ثانیه اتصال TCP برقرار شد، True و تأخیر (ms) را برمی‌گرداند"""
    try:
        start = time.time()
        with socket.create_connection((address, port), timeout=timeout):
            latency = (time.time() - start) * 1000
        return True, latency
    except:
        return False, 9999

# ---------------- دریافت کانفیگ‌ها از تلگرام ----------------
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

# ---------------- تست همه و ذخیره ----------------
if __name__ == "__main__":
    print("دریافت کانفیگ‌ها از تلگرام...")
    raw = extract_configs()
    print(f"{len(raw)} کانفیگ یافت شد. شروع تست اتصال TCP...")

    valid = []
    for link in raw:
        addr, port = get_address_port(link)
        if not addr or not port:
            print(f"⚠️ {link[:50]}... تجزیه نشد")
            continue
        ok, ping = tcp_test(addr, port, timeout=5)
        if ok:
            valid.append((ping, link))
            print(f"✅ {link[:60]}... پینگ: {ping:.0f}ms")
        else:
            print(f"❌ {link[:60]}... پورت بسته یا عدم پاسخ")

    # مرتب‌سازی بر اساس کمترین پینگ
    valid.sort(key=lambda x: x[0])
    final_links = [link for _, link in valid]

    # ذخیره‌سازی به صورت Base64 (مخصوص Subscription)
    content = "\n".join(final_links)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)

    print(f"پایان. {len(final_links)} کانفیگ معتبر در {CONFIG_FILE} ذخیره شد.")
