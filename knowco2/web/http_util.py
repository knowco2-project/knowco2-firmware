# knowco2/web/http_util.py
# ----------------------------------------------------------------------
# Low-level HTTP over raw sockets: response building, request parsing,
# URL decoding, and chunked/streamed body reading (incl. streaming large
# OTA uploads straight to a file while feeding the watchdog).
# ----------------------------------------------------------------------

import time
import json

from .. import state
from ..helpers import log


def _request_header(request_raw, name):
    """Return a lower-cost ASCII request header without retaining the body."""
    wanted = name.lower().encode("ascii") + b":"
    head = request_raw.split(b"\r\n\r\n", 1)[0].replace(b"\r\n", b"\n")
    for line in head.split(b"\n")[1:]:
        if line.lower().startswith(wanted):
            try:
                return line.split(b":", 1)[1].strip().decode("ascii", "ignore")
            except Exception:
                return ""
    return ""


def _authority_host(value):
    """Extract a lowercase host from an HTTP Host or Origin value."""
    value = (value or "").strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0]
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else ""
    return value.split(":", 1)[0]


def request_source_allowed(request_raw, allowed_hosts):
    """Validate browser source headers for a local state-changing request.

    Host validation blocks DNS-rebinding requests after an attacker hostname is
    rebound to the device. Origin and Sec-Fetch-Site checks block ordinary
    cross-site form/fetch requests. Native clients may omit Origin and
    Sec-Fetch-Site, but HTTP/1.1 state changes must always name this device in
    Host.
    """
    allowed = set()
    for value in allowed_hosts or ():
        host = _authority_host(value)
        if host:
            allowed.add(host)

    request_host = _authority_host(_request_header(request_raw, "host"))
    if not request_host or request_host not in allowed:
        return False

    fetch_site = _request_header(request_raw, "sec-fetch-site").strip().lower()
    if fetch_site == "cross-site":
        return False

    origin = _request_header(request_raw, "origin")
    if origin and _authority_host(origin) not in allowed:
        return False
    return True


def _feed_watchdog():
    """Feed the hardware watchdog during longer socket transfers."""
    try:
        if state._wd is not None:
            state._wd.feed()
    except Exception:
        pass


def send_all(conn, data, timeout=12.0):
    """Send every byte in *data* and report whether the transfer completed.

    CircuitPython sockets may perform short writes or raise EAGAIN while the
    Wi-Fi stack drains. The settings portal is large enough that a 2.5-second
    whole-response deadline can truncate it on mobile clients, so use a
    bounded but more realistic deadline and keep the watchdog alive.
    """
    mv = memoryview(data)
    total = 0
    length = len(mv)
    chunk_size = 1024
    start = time.monotonic()

    while total < length:
        if time.monotonic() - start > timeout:
            log(
                "send_to",
                "send_all timeout at",
                total,
                "of",
                length,
                "bytes",
                min_interval=2.0,
            )
            return False
        try:
            sent = conn.send(mv[total: total + chunk_size])
        except Exception as e:
            err = e.args[0] if e.args else None
            if err in (11, 35):  # EAGAIN / EWOULDBLOCK
                _feed_watchdog()
                time.sleep(0.01)
                continue
            log("send_err", "send_all error:", e, min_interval=1.0)
            return False
        if sent is None or sent <= 0:
            log(
                "send_closed",
                "send_all connection closed at",
                total,
                "of",
                length,
                "bytes",
                min_interval=1.0,
            )
            return False
        total += sent
        _feed_watchdog()

    return True


def build_response(status_code, content_type, body_bytes=b"", cors=False):
    """Build an HTTP response. CORS is opt-in and must be used only for a
    deliberately public, data-minimized read endpoint. State-changing,
    diagnostic, and HTML responses must never use wildcard CORS."""
    reason = {
        200: "OK",
        202: "Accepted",
        204: "No Content",
        302: "Found",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        413: "Payload Too Large",
        422: "Unprocessable Content",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status_code, "OK")
    headers = (
        "HTTP/1.1 %d %s\r\n" % (status_code, reason) +
        "Content-Type: %s\r\n" % content_type +
        "Cache-Control: no-store\r\n" +
        "Pragma: no-cache\r\n" +
        "Connection: close\r\n" +
        ("Access-Control-Allow-Origin: *\r\n" if cors else "") +
        "X-Content-Type-Options: nosniff\r\n" +
        "X-Frame-Options: SAMEORIGIN\r\n" +
        "Referrer-Policy: no-referrer\r\n"
    )
    if status_code != 204:
        headers += "Content-Length: %d\r\n" % len(body_bytes)
    headers += "\r\n"
    return headers.encode("utf-8"), body_bytes


def make_json_response(obj, status=200, cors=False):
    body = json.dumps(obj).encode("utf-8")
    return build_response(status, "application/json; charset=utf-8", body, cors=cors)


def make_html_response(html_str, status=200):
    body = html_str.encode("utf-8")
    return build_response(status, "text/html; charset=utf-8", body)


def sock_recv(conn, nbytes):
    if hasattr(conn, "recv"):
        return conn.recv(nbytes)
    if hasattr(conn, "recv_into"):
        buf = bytearray(nbytes)
        n = conn.recv_into(buf, nbytes)
        if n is None:
            return b""
        return bytes(buf[:n])
    return b""


def url_decode(s):
    if s is None:
        return ""
    try:
        s = s.replace('+', ' ')
        out = bytearray()
        i = 0
        while i < len(s):
            c = s[i]
            if c == '%' and i + 2 < len(s):
                try:
                    out.append(int(s[i + 1:i + 3], 16))
                    i += 3
                    continue
                except Exception:
                    pass
            out.extend(c.encode('utf-8'))
            i += 1
        return out.decode('utf-8', 'ignore')
    except Exception:
        return s


def parse_query(path):
    if "?" not in path:
        return path, {}
    route, qs = path.split("?", 1)
    params = {}
    for pair in qs.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        params[url_decode(k)] = url_decode(v)
    return route, params


def _split_headers_and_body(request_raw):
    """Return (header_bytes, already_received_body_bytes)."""
    sep = request_raw.find(b"\r\n\r\n")
    sep_len = 4
    if sep < 0:
        sep = request_raw.find(b"\n\n")
        sep_len = 2
    if sep < 0:
        return request_raw, b""
    return request_raw[:sep], request_raw[sep + sep_len:]


def _content_length(headers_only):
    """Parse Content-Length from either CRLF- or LF-delimited headers."""
    normalized = headers_only.replace(b"\r\n", b"\n")
    for line in normalized.split(b"\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                return int(line.split(b":", 1)[1].strip())
            except Exception:
                return 0
    return 0


def read_request_head(conn, max_bytes=2048, max_wait=0.6):
    data = b""
    start = time.monotonic()
    while (time.monotonic() - start) < max_wait and len(data) < max_bytes:
        try:
            chunk = sock_recv(conn, 512)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data or b"\n\n" in data:
                break
        except Exception:
            time.sleep(0.01)
    return data


def read_request_body(conn, request_raw, max_bytes=8192, max_wait=3.0):
    """Read a form POST body without discarding bytes read with the headers.

    TCP is a byte stream: Safari, Firefox, and app clients may deliver the
    headers and some or all of the body in the same recv(). read_request_head()
    intentionally returns that complete chunk, so the body prefix must be
    preserved before reading any remaining bytes from the socket.
    """
    try:
        headers_only, body_prefix = _split_headers_and_body(request_raw)
        content_length = _content_length(headers_only)
        if content_length <= 0:
            return b""

        content_length = min(content_length, max_bytes)
        body = bytearray(body_prefix[:content_length])
        start = time.monotonic()

        while len(body) < content_length and (time.monotonic() - start) < max_wait:
            try:
                chunk = sock_recv(conn, min(512, content_length - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            except Exception as e:
                err = e.args[0] if e.args else None
                if err not in (11, 35):
                    log("body_err", "request body read error:", e, min_interval=1.0)
                    break
                time.sleep(0.01)

        if len(body) < content_length:
            log(
                "body_short",
                "request body incomplete:",
                len(body),
                "of",
                content_length,
                "bytes",
                min_interval=1.0,
            )
        return bytes(body[:content_length])
    except Exception as e:
        log("body_err", "request body parse error:", e, min_interval=1.0)
        return b""


def stream_request_body_to_file(conn, headers_raw, dest_path, max_bytes=400000, max_wait=300.0):
    """Stream a POST body straight to a file in 512-byte chunks (no RAM
    buffering). Returns (success, message).

    _read_request_head may have already consumed body bytes past the blank
    line; we split headers_raw at the first header terminator and write the
    prefix first. The hardware watchdog is extended and fed per chunk so a
    large upload never causes a mid-write reset (which would wipe the
    filesystem).
    """
    try:
        headers_only, body_prefix = _split_headers_and_body(headers_raw)
        content_length = _content_length(headers_only)
        if content_length <= 0:
            return False, "Missing Content-Length header"
        if content_length > max_bytes:
            return False, "File too large (%d bytes, max %d)" % (content_length, max_bytes)

        # Extend watchdog for the write (normal 20 s would fire mid-upload).
        try:
            if state._wd is not None:
                state._wd.timeout = 90
        except Exception:
            pass

        try:
            conn.settimeout(30)
        except Exception:
            pass

        written = 0
        _empty_streak = 0
        start = time.monotonic()
        with open(dest_path, "wb") as f:
            if body_prefix:
                prefix = body_prefix[:content_length]
                f.write(prefix)
                written += len(prefix)
            while written < content_length:
                if (time.monotonic() - start) > max_wait:
                    return False, "Upload timed out after %d bytes" % written
                remaining = content_length - written
                try:
                    chunk = sock_recv(conn, min(512, remaining))
                except Exception:
                    _feed_watchdog()
                    time.sleep(0.05)
                    continue
                if not chunk:
                    _empty_streak += 1
                    if _empty_streak > 200:  # ~2 s of empty reads → closed
                        return False, "Connection closed after %d of %d bytes" % (written, content_length)
                    _feed_watchdog()
                    time.sleep(0.01)
                    continue
                _empty_streak = 0
                try:
                    f.write(chunk)
                except Exception as _we:
                    return False, "Disk write error after %d bytes: %s" % (written, str(_we))
                _feed_watchdog()
                written += len(chunk)
        if written < content_length:
            return False, "Incomplete upload: %d of %d bytes received" % (written, content_length)
        return True, "OK"
    except Exception as e:
        return False, "Stream error: " + str(e)


CAPTIVE_PATHS_204 = {
    "/generate_204", "/gen_204", "/ncsi.txt", "/connecttest.txt", "/success.txt", "/hotspot-detect.html",
    "/canonical.html", "/mobile/status.php", "/library/test/success.html", "/fwlink", "/fwlink/", "/redirect",
}
