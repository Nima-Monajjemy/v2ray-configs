import os, re, subprocess, tempfile, json, time, requests, shutil, base64, sqlite3
import urllib.parse

# ==================== تنظیمات ====================
MIXED_SOURCE_URL = "https://raw.githubusercontent.com/Farid-Karimi/Config-Collector/main/mixed_iran.txt"
CONFIG_FILE_MIXED = "tested_mixed.txt"
DB_FILE = "mixed_configs.db"

TEST_URL = "http://www.gstatic.com/generate_204"
TEST_TIMEOUT = 5.0
MAX_TEST = 1000
BATCH_SIZE = 50
EXPIRY_HOURS = 6
MAX_FAILURES = 1
PURGE_INTERVAL = 2

# ==================== دریافت کانفیگ‌ها ====================
def extract_mixed_configs():
    print("📡 در حال دریافت کانفیگ‌های لیست ترکیبی...")
    try:
        r = requests.get(MIXED_SOURCE_URL, timeout=15)
        text = r.text
        try:
            text = base64.b64decode(text).decode('utf-8')
        except:
            pass
        found = re.findall(r'(?:vless|vmess|trojan|ss)://\S+', text)
        unique_configs = list(set(found))
        print(f"📋 {len(unique_configs)} کانفیگ از لیست ترکیبی یافت شد.")
        return unique_configs
    except Exception as e:
        print(f"⚠️ خطا در دریافت لیست ترکیبی: {e}")
        return []

# ==================== تغییر نام و پرچم ====================
def get_flag_emoji(country_code):
    if not country_code or len(country_code) != 2: return "🌍"
    return "".join(chr(ord(c) + 127397) for c in country_code.upper())

def rename_config(link, country_name, country_code):
    prefix = f"[{get_flag_emoji(country_code)} {country_name}] "
    try:
        if link.startswith("vmess://"):
            b64 = link[8:]
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            data = json.loads(base64.b64decode(b64).decode('utf-8'))
            old_name = data.get("ps", "")
            data["ps"] = prefix + old_name
            new_b64 = base64.b64encode(json.dumps(data).encode('utf-8')).decode('utf-8')
            return "vmess://" + new_b64
        else:
            if "#" in link:
                base, old_name = link.split("#", 1)
                old_name = urllib.parse.unquote(old_name)
                new_name = urllib.parse.quote(prefix + old_name)
                return f"{base}#{new_name}"
            else:
                return f"{link}#{urllib.parse.quote(prefix.strip())}"
    except:
        return link

# ==================== موتور تست Xray ====================
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
    try:
        if link.startswith("vmess://"):
            b64 = link[8:]
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            decoded = json.loads(base64.b64decode(b64).decode('utf-8'))
            out = {
                "protocol": "vmess",
                "settings": {"vnext": [{"address": decoded["add"], "port": int(decoded["port"]), "users": [{"id": decoded["id"], "security": decoded.get("scy", "auto")}]}]},
                "streamSettings": {"network": decoded.get("net", "tcp")}
            }
            if decoded.get("net") == "ws":
                out["streamSettings"]["wsSettings"] = {"path": decoded.get("path", "/"), "headers": {"Host": decoded.get("host", decoded["add"])} if decoded.get("host") else {}}
            if decoded.get("tls") == "tls":
                out["streamSettings"]["security"] = "tls"
                out["streamSettings"]["tlsSettings"] = {"serverName": decoded.get("sni", decoded["add"])}
            return out

        elif link.startswith("ss://"):
            parsed = urllib.parse.urlparse(link)
            userinfo = parsed.username
            if userinfo:
                try:
                    padded = userinfo + '=' * (4 - len(userinfo) % 4) if len(userinfo) % 4 != 0 else userinfo
                    decoded = base64.b64decode(padded).decode('utf-8')
                    if ':' in decoded: method, password = decoded.split(':', 1)
                    else: method, password = "aes-256-gcm", decoded
                except:
                    if ':' in userinfo: method, password = userinfo.split(':', 1)
                    else: method, password = "aes-256-gcm", userinfo
            else: return None
            return {
                "protocol": "shadowsocks",
                "settings": {"servers": [{"address": parsed.hostname, "port": int(parsed.port), "method": method, "password": password}]},
                "streamSettings": {"network": "tcp", "security": "none"}
            }

        elif link.startswith("vless://") or link.startswith("trojan://"):
            parsed = urllib.parse.urlparse(link)
            protocol = "vless" if link.startswith("vless://") else "trojan"
            if protocol == "vless":
                settings = {"vnext": [{"address": parsed.hostname, "port": parsed.port, "users": [{"id": parsed.username, "encryption": "none", "flow": ""}]}]}
            else:
                settings = {"servers": [{"address": parsed.hostname, "port": parsed.port, "password": parsed.username}]}

            params = urllib.parse.parse_qs(parsed.query)
            def get_param(key, default=""): return params.get(key, [default])[0]

            network = get_param("type", "tcp")
            security = get_param("security", "none")
            sni = get_param("sni", parsed.hostname)
            host = get_param("host", "")
            path = get_param("path", "/")
            header_type = get_param("headerType", "none")
            alpn = get_param("alpn", "")
            fp = get_param("fp", "")
            flow = get_param("flow", "")
            
            if protocol == "vless" and flow: settings["vnext"][0]["users"][0]["flow"] = flow

            outbound = {"protocol": protocol, "settings": settings, "streamSettings": {"network": network, "security": security}}

            if network == "ws": outbound["streamSettings"]["wsSettings"] = {"path": path, "headers": {"Host": host} if host else {}}
            elif network == "tcp":
                if header_type == "http": outbound["streamSettings"]["tcpSettings"] = {"header": {"type": "http", "request": {"headers": {"Host": host} if host else {}, "path": path if path != "/" else "/"}}}
                elif header_type and header_type != "none": outbound["streamSettings"]["tcpSettings"] = {"header": {"type": header_type}}
            elif network == "grpc": outbound["streamSettings"]["grpcSettings"] = {"serviceName": path.lstrip("/"), "multiMode": False}
            elif network == "xhttp": outbound["streamSettings"]["xhttpSettings"] = {"mode": get_param("mode", "auto"), "path": path, "host": host}
            elif network == "httpupgrade": outbound["streamSettings"]["httpupgradeSettings"] = {"path": path, "host": host}

            if security == "tls":
                tls_settings = {"serverName": sni, "allowInsecure": get_param("allowInsecure", "0") == "1"}
                if alpn: tls_settings["alpn"] = alpn.split(",")
                if fp: tls_settings["fingerprint"] = fp
                outbound["streamSettings"]["tlsSettings"] = tls_settings
            elif security == "reality":
                outbound["streamSettings"]["realitySettings"] = {"serverName": sni, "fingerprint": fp if fp else "chrome", "publicKey": get_param("pbk", ""), "shortId": get_param("sid", ""), "spiderX": get_param("spx", "")}

            return outbound
    except: return None

def test_config(xray_bin, link, get_geoip=False):
    outbound = parse_link_to_outbound(link)
    if not outbound: return False, 9999, "", ""

    config = {"inbounds": [{"listen": "127.0.0.1", "port": 10808, "protocol": "socks", "settings": {"auth": "noauth"}}], "outbounds": [outbound]}
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        conf_path = f.name
        
    proc = subprocess.Popen([xray_bin, "run", "-c", conf_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    
    res = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{time_total}", "--socks5-hostname", "127.0.0.1:10808", TEST_URL, "--connect-timeout", str(TEST_TIMEOUT)], capture_output=True, text=True)
    
    latency = 9999
    is_ok = False
    country_name, country_code = "Unknown", "XX"

    if res.returncode == 0 and res.stdout.strip():
        latency = float(res.stdout.strip()) * 1000
        is_ok = True
        
        if get_geoip:
            try:
                geo_res = subprocess.run(["curl", "-s", "--socks5-hostname", "127.0.0.1:10808", "http://ip-api.com/json", "--connect-timeout", "4"], capture_output=True, text=True)
                if geo_res.returncode == 0:
                    geo_data = json.loads(geo_res.stdout)
                    country_name = geo_data.get("country", "Unknown")
                    country_code = geo_data.get("countryCode", "XX")
            except: pass
            time.sleep(1.5) # جلوگیری از Rate Limit سایت ip-api

    proc.terminate()
    try: proc.wait(2)
    except: proc.kill()
    os.unlink(conf_path)
    
    return is_ok, latency, country_name, country_code

# ==================== پایگاه داده ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS configs
                 (config_hash TEXT PRIMARY KEY, real_delay REAL, last_test_time REAL, fail_count INTEGER DEFAULT 0, country_name TEXT, country_code TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sys_state (id INTEGER PRIMARY KEY, run_count INTEGER)''')
    c.execute("INSERT OR IGNORE INTO sys_state (id, run_count) VALUES (1, 0)")
    conn.commit()
    conn.close()

def execute_db(query, args=()):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(query, args)
    res = c.fetchall()
    conn.commit()
    conn.close()
    return res

# ==================== هسته مرکزی ====================
def process_configs(xray_bin, raw_configs):
    print(f"\n🌍 شروع پردازش {len(raw_configs)} کانفیگ از لیست ترکیبی...\n")
    cached = {r[0]: r[1] for r in execute_db("SELECT config_hash, real_delay FROM configs")}
    
    for i, link in enumerate(raw_configs, 1):
        if link in cached: continue
        ok, delay, c_name, c_code = test_config(xray_bin, link, get_geoip=True)
        if ok:
            execute_db("INSERT OR REPLACE INTO configs VALUES (?, ?, ?, ?, ?, ?)", (link, delay, time.time(), 0, c_name, c_code))
            print(f"[{i}/{len(raw_configs)}] ✅ متصل شد ({c_code}): {delay:.0f}ms")
        else:
            print(f"[{i}/{len(raw_configs)}] ❌ ناموفق")

    print("\n🔁 بازبینی کانفیگ‌های قدیمی...")
    expired = execute_db(f"SELECT config_hash, fail_count FROM configs WHERE last_test_time < {time.time() - EXPIRY_HOURS * 3600} LIMIT 40")
    for link, fails in expired:
        ok, delay, _, _ = test_config(xray_bin, link, get_geoip=False)
        if ok:
            execute_db("UPDATE configs SET real_delay=?, last_test_time=?, fail_count=0 WHERE config_hash=?", (delay, time.time(), link))
            print(f"🔁 ✅ متصل شد")
        else:
            if fails + 1 >= MAX_FAILURES: 
                execute_db("DELETE FROM configs WHERE config_hash=?", (link,))
                print("   🗑️ حذف شد")
            else: 
                execute_db("UPDATE configs SET fail_count=?, last_test_time=? WHERE config_hash=?", (fails+1, time.time(), link))
                print("🔁 ❌ ناموفق")

    final_list = []
    valid = execute_db("SELECT config_hash, country_name, country_code FROM configs ORDER BY real_delay ASC")
    for link, c_name, c_code in valid:
        final_list.append(rename_config(link, c_name, c_code))

    content = base64.b64encode("\n".join(final_list).encode()).decode()
    with open(CONFIG_FILE_MIXED, "w") as f: f.write(content)
    print(f"🎯 لیست نهایی با {len(final_list)} کانفیگ تغییرنام یافته ذخیره شد.")

def purge_all(xray_bin):
    print("\n🧹 شروع پالایشِ کاملِ دیتابیس...")
    links = execute_db("SELECT config_hash FROM configs")
    for (link,) in links:
        ok, _, _, _ = test_config(xray_bin, link, False)
        if not ok: execute_db("DELETE FROM configs WHERE config_hash=?", (link,))
    print("🧹 پالایش کامل به پایان رسید.")

if __name__ == "__main__":
    init_db()
    xray_bin = download_xray()
    
    run_count = execute_db("SELECT run_count FROM sys_state WHERE id=1")[0][0]
    
    if run_count >= PURGE_INTERVAL:
        purge_all(xray_bin)
        execute_db("UPDATE sys_state SET run_count=0 WHERE id=1")
    else:
        execute_db("UPDATE sys_state SET run_count=? WHERE id=1", (run_count + 1,))
        raw_mixed = extract_mixed_configs()
        if raw_mixed: 
            process_configs(xray_bin, raw_mixed[:MAX_TEST])

    shutil.rmtree(os.path.dirname(xray_bin), ignore_errors=True)
