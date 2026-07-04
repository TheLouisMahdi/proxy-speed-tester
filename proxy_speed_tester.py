"""
HTTP Proxy Speed Tester

Tests HTTP CONNECT proxies, measures tunnel setup latency and download speed,
and exports working proxies sorted by speed or ping.

Use only with proxies you own or are authorized to test.
"""

import os
import re
import ssl
import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= Settings =================

INPUT_FILES = ["proxy.txt", "proxy", "out.txt"]

# Psiphon-ready output: ip:port only
OUTPUT_FILE = "fast_out.txt"

# Detailed speed and ping output
DETAILS_FILE = "speed_details.txt"

# Rejected proxy output
BAD_FILE = "bad.txt"

# Increase carefully depending on your network/device.
MAX_WORKERS = 8
TIMEOUT = 8

# Speed test size for each proxy:
# 256KB = lighter
# 512KB = balanced
# 1MB = more accurate but uses more data
SPEED_TEST_BYTES = 512 * 1024

# Number of speed test attempts.
# Use 1 to save data.
SPEED_ATTEMPTS = 1

# Sorting mode:
# "speed" => highest speed first
# "ping"  => lowest ping first
SORT_BY = "speed"

# Speed test server
TEST_HOST = "speed.cloudflare.com"
TEST_PORT = 443
TEST_PATH = f"/__down?bytes={SPEED_TEST_BYTES}"

CLEAR_OLD_OUTPUT = True

# ============================================

results = []
results_lock = threading.Lock()
file_lock = threading.Lock()
print_lock = threading.Lock()


def is_valid_ip(ip):
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        return all(0 <= int(p) <= 255 for p in parts)
    except Exception:
        return False


def normalize_line(line):
    """
    Supported formats:
    1.2.3.4:8080
    1.2.3.4.8080
    http://1.2.3.4:8080
    1.2.3.4:8080 | ping=100ms | speed=200KB/s
    """
    line = line.strip()

    if not line:
        return None

    if line.startswith("#"):
        return None

    # Remove old details if the line came from a previous output file.
    line = line.split("|")[0].strip()

    line = line.replace(" ", "")

    # Remove scheme.
    line = re.sub(r"^(http|https|socks4|socks5|socks5h)://", "", line, flags=re.I)

    # Remove possible path.
    line = line.split("/")[0]

    # ip:port
    m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$", line)
    if m:
        ip = m.group(1)
        port = int(m.group(2))
        if is_valid_ip(ip) and 1 <= port <= 65535:
            return ip, port, f"{ip}:{port}"

    # ip.port
    m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3})\.(\d{1,5})$", line)
    if m:
        ip = m.group(1)
        port = int(m.group(2))
        if is_valid_ip(ip) and 1 <= port <= 65535:
            return ip, port, f"{ip}:{port}"

    return None


def load_proxies():
    input_file = None

    for name in INPUT_FILES:
        if os.path.exists(name):
            input_file = name
            break

    if input_file is None:
        print("proxy.txt or proxy file was not found.")
        return []

    proxies = []
    seen = set()

    with open(input_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            item = normalize_line(line)
            if item:
                ip, port, text = item
                if text not in seen:
                    proxies.append((ip, port, text))
                    seen.add(text)

    print(f"Input file: {input_file}")
    return proxies


def read_until_headers_done(sock, max_bytes=16384):
    data = b""

    while b"\r\n\r\n" not in data and len(data) < max_bytes:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk

    return data


def connect_http_proxy(ip, port):
    """
    Connect to an HTTP proxy and wait for CONNECT 200.

    Returns:
        tuple: socket, ping_ms, status
    """
    start = time.perf_counter()

    try:
        s = socket.create_connection((ip, port), timeout=TIMEOUT)
        s.settimeout(TIMEOUT)

        req = (
            f"CONNECT {TEST_HOST}:{TEST_PORT} HTTP/1.1\r\n"
            f"Host: {TEST_HOST}:{TEST_PORT}\r\n"
            "User-Agent: Mozilla/5.0\r\n"
            "Proxy-Connection: keep-alive\r\n"
            "\r\n"
        )

        s.sendall(req.encode("ascii", errors="ignore"))

        raw = read_until_headers_done(s)

        if not raw:
            s.close()
            return None, None, "empty response"

        text = raw.decode("iso-8859-1", errors="replace")
        first_line = text.split("\r\n")[0].strip()

        ping_ms = round((time.perf_counter() - start) * 1000, 2)

        if not first_line.startswith("HTTP/"):
            s.close()
            return None, None, f"not http response: {repr(raw[:16])}"

        parts = first_line.split()
        if len(parts) < 2:
            s.close()
            return None, None, f"bad status line: {first_line}"

        status_code = parts[1]

        if status_code != "200":
            s.close()
            return None, None, f"http status {status_code}"

        return s, ping_ms, first_line

    except socket.timeout:
        return None, None, "timeout"
    except ConnectionRefusedError:
        return None, None, "connection refused"
    except OSError as e:
        return None, None, f"os error: {e}"
    except Exception as e:
        return None, None, f"error: {e}"


def speed_test_through_proxy(ip, port):
    """
    After CONNECT, create a TLS tunnel and download test data from Cloudflare.
    """
    raw_sock, ping_ms, status = connect_http_proxy(ip, port)

    if raw_sock is None:
        return False, None, None, status

    try:
        context = ssl.create_default_context()

        with context.wrap_socket(raw_sock, server_hostname=TEST_HOST) as tls_sock:
            tls_sock.settimeout(TIMEOUT)

            request = (
                f"GET {TEST_PATH} HTTP/1.1\r\n"
                f"Host: {TEST_HOST}\r\n"
                "User-Agent: Mozilla/5.0\r\n"
                "Accept: */*\r\n"
                "Accept-Encoding: identity\r\n"
                "Connection: close\r\n"
                "\r\n"
            )

            tls_sock.sendall(request.encode("ascii", errors="ignore"))

            start = time.perf_counter()

            data = b""
            header_done = False
            body_bytes = 0
            status_line = ""

            while True:
                try:
                    chunk = tls_sock.recv(16384)
                except socket.timeout:
                    break

                if not chunk:
                    break

                if not header_done:
                    data += chunk

                    if b"\r\n\r\n" in data:
                        header, body = data.split(b"\r\n\r\n", 1)
                        header_text = header.decode("iso-8859-1", errors="replace")
                        status_line = header_text.split("\r\n")[0].strip()

                        if not status_line.startswith("HTTP/"):
                            return False, ping_ms, None, "download not http"

                        parts = status_line.split()
                        if len(parts) < 2 or parts[1] != "200":
                            return False, ping_ms, None, f"download {status_line}"

                        body_bytes += len(body)
                        header_done = True
                        data = b""
                else:
                    body_bytes += len(chunk)

            elapsed = time.perf_counter() - start

            if body_bytes <= 0 or elapsed <= 0:
                return False, ping_ms, None, "no download data"

            speed_kbps = round((body_bytes / 1024) / elapsed, 2)
            speed_mbps = round((body_bytes * 8) / (elapsed * 1_000_000), 2)

            return True, ping_ms, {
                "bytes": body_bytes,
                "seconds": round(elapsed, 2),
                "kbps": speed_kbps,
                "mbps": speed_mbps,
                "download_status": status_line,
            }, status

    except ssl.SSLError as e:
        return False, ping_ms, None, f"ssl error: {e}"
    except socket.timeout:
        return False, ping_ms, None, "download timeout"
    except Exception as e:
        return False, ping_ms, None, f"download error: {e}"


def best_speed_test(ip, port):
    best = None
    last_reason = ""

    for _ in range(SPEED_ATTEMPTS):
        ok, ping_ms, speed_info, status = speed_test_through_proxy(ip, port)

        if ok and speed_info:
            item = {
                "ping": ping_ms,
                "kbps": speed_info["kbps"],
                "mbps": speed_info["mbps"],
                "bytes": speed_info["bytes"],
                "seconds": speed_info["seconds"],
                "status": status,
            }

            if best is None or item["kbps"] > best["kbps"]:
                best = item
        else:
            last_reason = status

    if best is None:
        return False, None, last_reason

    return True, best, "ok"


def sort_results():
    if SORT_BY == "ping":
        return sorted(results, key=lambda x: (x["ping"], -x["kbps"]))
    return sorted(results, key=lambda x: (-x["kbps"], x["ping"]))


def rewrite_outputs():
    with file_lock:
        sorted_items = sort_results()

        temp_out = OUTPUT_FILE + ".tmp"
        temp_details = DETAILS_FILE + ".tmp"

        # Psiphon-ready output.
        with open(temp_out, "w", encoding="utf-8") as f:
            for item in sorted_items:
                f.write(item["proxy"] + "\n")
            f.flush()

        # Detailed output.
        with open(temp_details, "w", encoding="utf-8") as f:
            for item in sorted_items:
                f.write(
                    f'{item["proxy"]} | '
                    f'speed={item["kbps"]}KB/s | '
                    f'mbps={item["mbps"]}Mbps | '
                    f'ping={item["ping"]}ms | '
                    f'time={item["seconds"]}s | '
                    f'data={item["bytes"]}B\n'
                )
            f.flush()

        os.replace(temp_out, OUTPUT_FILE)
        os.replace(temp_details, DETAILS_FILE)


def save_bad(proxy, reason):
    with file_lock:
        with open(BAD_FILE, "a", encoding="utf-8") as f:
            f.write(f"{proxy} | {reason}\n")
            f.flush()


def check_one(item):
    ip, port, proxy_text = item
    ok, speed_data, reason = best_speed_test(ip, port)
    return ok, proxy_text, speed_data, reason


def main():
    proxies = load_proxies()

    if not proxies:
        print("No valid proxies found.")
        return

    if CLEAR_OLD_OUTPUT:
        open(OUTPUT_FILE, "w", encoding="utf-8").close()
        open(DETAILS_FILE, "w", encoding="utf-8").close()
        open(BAD_FILE, "w", encoding="utf-8").close()

    print(f"Number of proxies to speed test: {len(proxies)}")
    print(f"Speed test size per proxy: {round(SPEED_TEST_BYTES / 1024)}KB")
    print(f"Sort by: {SORT_BY}")
    print("-" * 70)

    checked = 0
    good = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(check_one, item) for item in proxies]

        for future in as_completed(futures):
            checked += 1

            try:
                ok, proxy_text, speed_data, reason = future.result()
            except Exception:
                continue

            if ok:
                good += 1

                with results_lock:
                    results.append({
                        "proxy": proxy_text,
                        "ping": speed_data["ping"],
                        "kbps": speed_data["kbps"],
                        "mbps": speed_data["mbps"],
                        "bytes": speed_data["bytes"],
                        "seconds": speed_data["seconds"],
                        "status": speed_data["status"],
                    })

                    rewrite_outputs()

                with print_lock:
                    print(
                        f'[FAST OK] {proxy_text} | '
                        f'{speed_data["kbps"]}KB/s | '
                        f'{speed_data["mbps"]}Mbps | '
                        f'ping={speed_data["ping"]}ms'
                    )

            else:
                save_bad(proxy_text, reason)

                with print_lock:
                    print(f"[BAD/SLOW] {proxy_text} | {reason}")

            if checked % 25 == 0:
                with print_lock:
                    print(f"Progress: {checked}/{len(proxies)} | Good: {good}")

    print("-" * 70)
    print("Finished.")
    print(f"Total tested: {checked}")
    print(f"Working tested proxies: {good}")
    print(f"Psiphon-ready output: {OUTPUT_FILE}")
    print(f"Speed details: {DETAILS_FILE}")
    print(f"Rejected proxies: {BAD_FILE}")


if __name__ == "__main__":
    main()
