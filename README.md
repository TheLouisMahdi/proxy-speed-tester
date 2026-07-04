# Proxy Speed Tester

A lightweight Python tool for testing **HTTP CONNECT proxies**, measuring tunnel latency and download speed, and exporting working proxies sorted by speed or ping.

This project is useful when you have a list of HTTP proxies and want to quickly find which ones are actually usable, stable, and faster.

> Use this tool only with proxies you own or are authorized to test.

## Features

- Tests HTTP CONNECT proxy support
- Measures connection latency / ping
- Performs a real HTTPS download speed test through the proxy
- Sorts working proxies by speed or ping
- Removes duplicate proxy entries
- Supports common input formats
- Generates Psiphon-ready `ip:port` output
- Saves rejected proxies with failure reasons
- Uses only Python standard library modules

## Supported input formats

Put your proxies in a local file named `proxy.txt`, `proxy`, or `out.txt`.

Supported formats:

```text
1.2.3.4:8080
1.2.3.4.8080
http://1.2.3.4:8080
1.2.3.4:8080 | ping=100ms | speed=200KB/s
```

## Output files

The script creates these files locally:

| File | Description |
| --- | --- |
| `fast_out.txt` | Working proxies only, sorted and ready to copy/use |
| `speed_details.txt` | Detailed results with speed, ping, time, and data size |
| `bad.txt` | Rejected proxies with error reason |

These generated files are ignored by Git and should not be committed.

## Usage

Clone the repository:

```bash
git clone https://github.com/TheLouisMahdi/proxy-speed-tester.git
cd proxy-speed-tester
```

Create your proxy list:

```bash
copy proxy.example.txt proxy.txt
```

On Linux/macOS:

```bash
cp proxy.example.txt proxy.txt
```

Edit `proxy.txt` and put your own proxies inside it.

Run the script:

```bash
python proxy_speed_tester.py
```

On some systems you may need:

```bash
python3 proxy_speed_tester.py
```

## Configuration

You can edit these values at the top of `proxy_speed_tester.py`:

```python
MAX_WORKERS = 8
TIMEOUT = 8
SPEED_TEST_BYTES = 512 * 1024
SPEED_ATTEMPTS = 1
SORT_BY = "speed"
```

Sorting modes:

```python
SORT_BY = "speed"  # highest speed first
SORT_BY = "ping"   # lowest ping first
```

## Notes

This tool checks **HTTP CONNECT proxies**. It does not fully test SOCKS4 or SOCKS5 proxies, even if a SOCKS-style URL is present in the input. The script normalizes the input to `ip:port`, then tests it as an HTTP CONNECT proxy.

The default speed test target is:

```text
speed.cloudflare.com
```

## Example terminal output

```text
Input file: proxy.txt
Number of proxies to speed test: 120
Speed test size per proxy: 512KB
Sort by: speed
----------------------------------------------------------------------
[FAST OK] 1.2.3.4:8080 | 430.21KB/s | 3.52Mbps | ping=180.4ms
[BAD/SLOW] 5.6.7.8:3128 | timeout
Progress: 25/120 | Good: 4
----------------------------------------------------------------------
Finished.
Total tested: 120
Working tested proxies: 12
Psiphon-ready output: fast_out.txt
Speed details: speed_details.txt
Rejected proxies: bad.txt
```

## Safety and responsible use

- Do not scan random IP ranges.
- Do not test proxies you are not allowed to use.
- Do not upload real proxy lists to public repositories.
- Keep `proxy.txt`, `fast_out.txt`, `speed_details.txt`, and `bad.txt` private.

## License

This project is released under the MIT License.
