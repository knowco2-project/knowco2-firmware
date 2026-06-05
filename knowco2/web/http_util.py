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


def send_all(conn, data, timeout=2.5):
    mv = memoryview(data)
    total = 0
    length = len(mv)
    CHUNK = 512
    start = time.monotonic()

    while total < length:
        if time.monotonic() - start > timeout:
            log("send_to", "send_all timeout at", total, "of", length, "bytes", min_interval=2.0)
            break
        try:
            sent = conn.send(mv[total: total + CHUNK])
        except Exception as e:
            err = e.args[0] if e.args else None
            if err == 11:  # EAGAIN / EWOULDBLOCK
                time.sleep(0.01)
                continue
            log("send_err", "send_all error:", e, min_interval=1.0)
            break
        if sent is None or sent <= 0:
            break
        total += sent


def build_response(status_code, content_type, body_bytes=b""):
    reason = {200: "OK", 204: "No Content", 302: "Found",
              404: "Not Found", 405: "Method Not Allowed"}.get(status_code, "OK")
    headers = (
        "HTTP/1.1 %d %s\r\n" % (status_code, reason) +
        "Content-Type: %s\r\n" % content_type +
        "Cache-Control: no-store\r\n" +
        "Pragma: no-cache\r\n" +
        "Connection: close\r\n" +
        "Access-Control-Allow-Origin: *\r\n" +
        "X-Content-Type-Options: nosniff\r\n" +
        "X-Frame-Options: SAMEORIGIN\r\n" +
        "Referrer-Policy: no-referrer\r\n"
    )
    if status_code != 204:
        headers += "Content-Length: %d\r\n" % len(body_bytes)
    headers += "\r\n"
    return headers.encode("utf-8"), body_bytes


def make_json_response(obj, status=200):
    body = json.dumps(obj).encode("utf-8")
    return build_response(status, "application/json; charset=utf-8", body)


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


def read_request_body(conn, headers_raw, max_bytes=8192, max_wait=3.0):
    """Read a POST body. headers_raw is the already-read request head."""
    try:
        content_length = 0
        for line in headers_raw.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except Exception:
                    pass
                break
        if content_length <= 0:
            return b""
        content_length = min(content_length, max_bytes)
        body = b""
        start = time.monotonic()
        while len(body) < content_length and (time.monotonic() - start) < max_wait:
            try:
                chunk = sock_recv(conn, min(512, content_length - len(body)))
                if not chunk:
                    break
                body += chunk
            except Exception:
                time.sleep(0.01)
        return body
    except Exception:
        return b""


def stream_request_body_to_file(conn, headers_raw, dest_path, max_bytes=400000, max_wait=300.0):
    """Stream a POST body straight to a file in 512-byte chunks (no RAM
    buffering). Returns (success, message).

    _read_request_head may have already consumed body bytes past the blank
    line; we split headers_raw at the first CRLFCRLF and write the prefix
    first. The hardware watchdog is extended and fed per chunk so a large
    upload never causes a mid-write reset (which would wipe the filesystem).
    """
    try:
        sep = headers_raw.find(b"\r\n\r\n")
        if sep >= 0:
            headers_only = headers_raw[:sep]
            body_prefix = headers_raw[sep + 4:]
        else:
            headers_only = headers_raw
            body_prefix = b""

        content_length = 0
        for line in headers_only.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except Exception:
                    pass
                break
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
                f.write(body_prefix)
                written += len(body_prefix)
            while written < content_length:
                if (time.monotonic() - start) > max_wait:
                    return False, "Upload timed out after %d bytes" % written
                remaining = content_length - written
                try:
                    chunk = sock_recv(conn, min(512, remaining))
                except Exception:
                    try:
                        if state._wd is not None:
                            state._wd.feed()
                    except Exception:
                        pass
                    time.sleep(0.05)
                    continue
                if not chunk:
                    _empty_streak += 1
                    if _empty_streak > 200:  # ~2 s of empty reads → closed
                        return False, "Connection closed after %d of %d bytes" % (written, content_length)
                    try:
                        if state._wd is not None:
                            state._wd.feed()
                    except Exception:
                        pass
                    time.sleep(0.01)
                    continue
                _empty_streak = 0
                try:
                    f.write(chunk)
                except Exception as _we:
                    return False, "Disk write error after %d bytes: %s" % (written, str(_we))
                try:
                    if state._wd is not None:
                        state._wd.feed()
                except Exception:
                    pass
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
