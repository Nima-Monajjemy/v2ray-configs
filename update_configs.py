import os, re, socket, time, base64, json
from telethon import TelegramClient
from telethon.sessions import StringSession

# ---------------- تنظیمات ----------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STR = os.environ["SESSION_STRING"]
CHANNEL = "@SOSkeyNET"
CONFIG_FILE = "configs.txt"

# ---------------- استخراج آدرس و پورت (روش regex محکم) ----------------
def extract_host_port(link):
    """آدرس و پورت را از انواع لینک‌های vless, vmess, trojan استخراج می‌کند"""
    try:
        # ۱. حالت vless و trojan: ساختمان @host:port
        match = re.search(r'@([^/:]+):(\d+)', link)
        if match:
            return match.group(1), int(match.group(2))

        # ۲. حالت vmess: ابتدا base64 را دیکد می‌کنیم
        if link.startswith("vmess://"):
            b64 = link[8:]
            padded = b64 + '=' * (4 - len(b64) % 4) if len(b64) % 4 != 0 else b64
            decoded = base64.b64decode(padded).decode('utf-8')
            info = json.loads(decoded)
            return info.get("add", ""), int(info.get("port", 0))
    except Exception as e:
        print(f"   ↳ خطا در تجزیه: {e}")
    return None, None

# ---------------- تست اتصال TCP ساده ----------------
def tcp_test(address, port, timeout=5):
    try:
        start = time.time()
        with socket.create_connection((address, port), timeout=timeout):
            latency = (time.time() - start) * 1000
        return True, latency
    except Exception as e:
        # print(f"   ↳ خطای اتصال: {e}")  # در صورت نیاز می‌توانید کامنت را بردارید
        return False, 9999

# ---------------- دریافت کانفیگ‌ها از تلگرام ----------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

def fetch_links():
    links = set()
    with client:
        for msg in client.iter_messages(CHANNEL, limit=100):
            if msg.text:
                # استخراج هر چیزی که با vless:// یا vmess:// یا trojan:// شروع شود
                found = re.findall(r'(?:vless|vmess|trojan)://\S+', msg.text)
                for lnk in found:
                    links.add(lnk)
    return list(links)

# ---------------- اجرای اصلی ----------------
if __name__ == "__main__":
    print("دریافت کانفیگ‌ها از تلگرام...")
    raw_links = fetch_links()
    print(f"{len(raw_links)} کانفیگ یافت شد. شروع تست TCP...\n")

    valid = []
    for link in raw_links:
        # نمایش ۶۰ کاراکتر اول لینک برای شناسایی
        short = link[:70] + ("..." if len(link) > 70 else "")
        host, port = extract_host_port(link)
        if not host or not port:
            print(f"⚠️ {short}  ← تجزیه نشد (host={host}, port={port})")
            continue

        ok, ping = tcp_test(host, port, timeout=5)
        if ok:
            valid.append((ping, link))
            print(f"✅ {short}  ← پینگ: {ping:.0f}ms")
        else:
            print(f"❌ {short}  ← پورت بسته یا عدم پاسخ")

    # مرتب‌سازی بر اساس تأخیر
    valid.sort(key=lambda x: x[0])
    final_links = [lnk for _, lnk in valid]

    # ذخیره‌سازی با فرمت Base64 (مناسب subscription)
    content = "\n".join(final_links)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(encoded)

    print(f"\nپایان. {len(final_links)} کانفیگ معتبر در {CONFIG_FILE} ذخیره شد.")
