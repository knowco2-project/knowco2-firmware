"""Bounded signed OTA verifier for CircuitPython 10.2.1.

RSA-2048 / exponent 65537 / strict PKCS#1 v1.5 SHA-256. Authenticate every
STORED member before executable changes, with signed exact-base/dependency
binding for scoped device-test updates. This is application-layer
protection, NOT secure boot or protection from physical service-volume access.
"""

import binascii
import struct

try:
    import hashlib
except ImportError:
    hashlib = None

from . import ota_trust

BOARD = "adafruit_feather_esp32s3_reverse_tft"
RUNTIME = "10.2.1"
MAX_ENTRIES = 160
MAX_MANIFEST = 32768
MAX_FILE = 524288
MAX_TOTAL = 2097152
MAX_PACKAGE_BYTES = 512 * 1024
MAX_ARCHIVE = MAX_PACKAGE_BYTES
MAX_SEQUENCE = 2147483647
MANIFEST_NAME = "ota-manifest.txt"
SIGNATURE_NAME = "ota-manifest.sig"
NVM_OFFSET = 16  # recovery.py owns only bytes 0 and 1.
NVM_RECORD_SIZE = 80
_DIGEST_INFO = binascii.unhexlify("3031300d060960864801650304020105000420")


class OTARejected(ValueError):
    """An untrusted/incompatible package or antirollback state was rejected."""


def _require(value, message):
    if not value:
        raise OTARejected(message)


def _hash(data=b""):
    _require(hashlib is not None, "Native SHA-256 unavailable")
    try:
        return hashlib.new("sha256", data)
    except Exception:
        raise OTARejected("Native SHA-256 unavailable")


def _equal(a, b):
    if len(a) != len(b):
        return False
    difference = 0
    for x, y in zip(a, b):
        difference |= x ^ y
    return difference == 0


def _feed(watchdog):
    if watchdog is not None:
        watchdog()


def _u16(data, offset):
    return data[offset] | data[offset + 1] << 8


def _u32(data, offset):
    return _u16(data, offset) | _u16(data, offset + 2) << 16


def _number(text, maximum):
    _require(isinstance(text, str) and 0 < len(text) <= 10,
             "Invalid bounded number")
    _require(all("0" <= c <= "9" for c in text), "Invalid bounded number")
    value = int(text)
    _require(str(value) == text and value <= maximum, "Invalid bounded number")
    return value


def valid_path(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 80:
        return False
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-" for c in name):
        return False
    if any(part in ("", ".", "..") or part.startswith(".") or part.endswith(".")
           for part in name.split("/")):
        return False
    if name in ("code.py", "boot.py"):
        return True
    return name.startswith(("knowco2/", "lib/", "assets/")) and not name.endswith(
        (".pyc", ".pyo", ".bak", ".tmp")) and "__pycache__" not in name


def _zip_entries(path):
    """Exact bounded ZIP subset; no descriptors, aliases or gaps."""
    entries = []
    with open(path, "rb") as source:
        source.seek(0, 2)
        size = source.tell()
        _require(22 <= size <= MAX_ARCHIVE, "OTA ZIP size outside bounds")
        source.seek(size - 22)
        end = source.read(22)
        _require(end[:4] == b"PK\x05\x06" and _u16(end, 20) == 0,
                 "ZIP comments/ZIP64/truncation are not supported")
        count = _u16(end, 10)
        cd_size, cd_start = _u32(end, 12), _u32(end, 16)
        _require(_u16(end, 4) == 0 and _u16(end, 6) == 0 and
                 _u16(end, 8) == count, "Multi-disk ZIP rejected")
        _require(3 <= count <= MAX_ENTRIES + 2 and cd_size <= 32768 and
                 cd_start + cd_size == size - 22, "Invalid ZIP directory bounds")
        source.seek(cd_start)
        seen = set()
        aliases = set()
        next_local = 0
        for _ in range(count):
            header = source.read(46)
            _require(len(header) == 46 and header[:4] == b"PK\x01\x02",
                     "Invalid ZIP directory entry")
            flags, method = _u16(header, 8), _u16(header, 10)
            name_len, extra_len, comment_len = (_u16(header, 28),
                                               _u16(header, 30), _u16(header, 32))
            compressed, uncompressed = _u32(header, 20), _u32(header, 24)
            local = _u32(header, 42)
            _require(flags in (0, 2048) and method == 0 and
                     compressed == uncompressed and uncompressed <= MAX_FILE,
                     "Signed OTA requires bounded STORED ZIP members")
            _require(1 <= name_len <= 80 and extra_len == 0 and comment_len == 0
                     and _u16(header, 34) == 0, "Unsupported ZIP metadata")
            unix_kind = (_u32(header, 38) >> 16) & 0xF000
            _require(unix_kind in (0, 0x8000), "ZIP links/directories rejected")
            name_bytes = source.read(name_len)
            _require(len(name_bytes) == name_len, "Truncated ZIP filename")
            try:
                name = name_bytes.decode("ascii")
            except Exception:
                raise OTARejected("Non-ASCII ZIP filename")
            _require(name in (MANIFEST_NAME, SIGNATURE_NAME) or valid_path(name),
                     "Unsafe ZIP path")
            alias = name.lower()
            if alias.endswith((".py", ".mpy")):
                alias = alias.rsplit(".", 1)[0]
            _require(name not in seen and alias not in aliases, "Duplicate ZIP path/alias")
            seen.add(name)
            aliases.add(alias)
            central_position = source.tell()
            _require(local == next_local and local + 30 + name_len <= cd_start,
                     "Overlapping or disordered ZIP entries")
            source.seek(local)
            local_header = source.read(30)
            _require(len(local_header) == 30 and local_header[:4] == b"PK\x03\x04",
                     "Invalid local ZIP header")
            _require(_u16(local_header, 6) == flags and _u16(local_header, 8) == method
                     and _u32(local_header, 14) == _u32(header, 16)
                     and _u32(local_header, 18) == compressed
                     and _u32(local_header, 22) == uncompressed
                     and _u16(local_header, 26) == name_len
                     and _u16(local_header, 28) == 0,
                     "Local/central ZIP metadata mismatch")
            _require(source.read(name_len) == name_bytes, "Local ZIP filename mismatch")
            data_start = local + 30 + name_len
            next_local = data_start + compressed
            _require(next_local <= cd_start, "ZIP member extends beyond payload")
            entries.append({"name": name, "local_off": local,
                            "data_start": data_start, "comp_size": compressed,
                            "uncomp_size": uncompressed, "method": method,
                            "crc": _u32(header, 16)})
            source.seek(central_position)
        _require(source.tell() == cd_start + cd_size and next_local == cd_start,
                 "Unexpected ZIP data")
    return entries


def _member_bytes(source, entry, maximum):
    _require(entry["method"] == 0 and entry["uncomp_size"] <= maximum,
             "OTA metadata must be bounded and STORED")
    source.seek(entry["data_start"])
    result = source.read(entry["uncomp_size"])
    _require(len(result) == entry["uncomp_size"], "Truncated OTA metadata")
    return result


def _verify_signature(manifest, signature, trust, watchdog):
    key = trust.PUBLIC_KEY_N_HEX
    _require(isinstance(key, str) and len(key) == 512 and
             all(c in "0123456789abcdef" for c in key), "OTA trust key not provisioned")
    modulus = int(key, 16)
    _require(modulus >= (1 << 2047) and modulus & 1, "Invalid RSA-2048 trust key")
    _require(len(signature) == 256, "Invalid RSA signature length")
    value = int.from_bytes(signature, "big")
    _require(value < modulus, "RSA signature outside modulus")
    _feed(watchdog)
    # Native modular exponentiation (CIRCUITPY_BUILTINS_POW3) avoids a Python
    # bignum loop. Hardware timing/watchdog qualification remains required.
    encoded = pow(value, 65537, modulus).to_bytes(256, "big")
    _feed(watchdog)
    tail = _DIGEST_INFO + _hash(manifest).digest()
    expected = b"\x00\x01" + b"\xff" * (256 - len(tail) - 3) + b"\x00" + tail
    _require(_equal(encoded, expected), "OTA signature verification failed")


def _manifest(manifest, trust):
    _require(len(manifest) <= MAX_MANIFEST and manifest.endswith(b"\n"),
             "Invalid signed manifest size/terminator")
    try:
        lines = manifest.decode("ascii").split("\n")
    except Exception:
        raise OTARejected("Invalid manifest encoding")
    _require(10 <= len(lines) <= MAX_ENTRIES + 10, "Invalid manifest entry count")
    _require(lines[:4] == ["KNOWCO2-OTA-1", "board=" + BOARD,
                          "runtime=" + RUNTIME, "key=" + trust.KEY_ID],
             "Manifest board/runtime/key mismatch")
    _require(lines[4] == "channel=" + ("TEST-ONLY" if trust.TEST_ONLY else "production"),
             "Manifest trust channel mismatch")
    _require(lines[5].startswith("sequence="), "Missing release sequence")
    sequence = _number(lines[5][9:], MAX_SEQUENCE)
    _require(sequence > 0, "Release sequence must be positive")
    _require(lines[6:9] == ["base_build=" + trust.BUILD_ID,
                           "library_set=" + trust.LIBRARY_SET_ID,
                           "mpy_abi=" + trust.MPY_ABI],
             "Manifest base build/dependency ABI mismatch")
    files = {}
    total = 0
    previous = ""
    for line in lines[9:-1]:
        parts = line.split(" ")
        _require(len(parts) == 3, "Invalid manifest file record")
        digest, size, name = parts
        _require(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest),
                 "Invalid file SHA-256")
        _require(valid_path(name) and name > previous, "Unsorted/unsafe manifest path")
        previous = name
        length = _number(size, MAX_FILE)
        total += length
        _require(total <= MAX_TOTAL, "OTA payload exceeds total limit")
        files[name] = (length, binascii.unhexlify(digest))
    _require("code.py" in files and "boot.py" in files, "OTA entrypoints required")
    return sequence, files


def _device_compatible():
    import sys
    if sys.implementation.name == "circuitpython":
        import board
        _require(getattr(board, "board_id", "") == BOARD, "Wrong device board")
        _require(tuple(sys.implementation.version[:3]) == (10, 2, 1),
                 "OTA requires qualified CircuitPython 10.2.1")


def _nvm_bytes(nvm):
    if nvm is None:
        try:
            import microcontroller
            nvm = microcontroller.nvm
        except Exception:
            raise OTARejected("Antirollback NVM unavailable")
    _require(nvm is not None and len(nvm) >= NVM_OFFSET + 2 * NVM_RECORD_SIZE,
             "Antirollback NVM unavailable")
    return nvm


def _record(raw):
    if raw in (b"\x00" * NVM_RECORD_SIZE, b"\xff" * NVM_RECORD_SIZE):
        return None
    if raw[:4] != b"KOT1" or raw[4] not in (1, 2) or raw[5:8] != b"\0\0\0":
        return False
    if not _equal(_hash(raw[:48]).digest(), raw[48:]):
        return False
    sequence = _u32(raw, 8)
    if not 1 <= sequence <= MAX_SEQUENCE or raw[44:48] != b"\0\0\0\0":
        return False
    return {"sequence": sequence, "digest": raw[12:44], "complete": raw[4] == 2}


def _state(nvm):
    records = []
    nonblank = False
    for slot in range(2):
        start = NVM_OFFSET + slot * NVM_RECORD_SIZE
        parsed = _record(bytes(nvm[start:start + NVM_RECORD_SIZE]))
        nonblank = nonblank or parsed is not None
        if parsed:
            records.append(parsed)
    _require(records or not nonblank, "Antirollback state corrupt; physical recovery required")
    if not records:
        return None
    records.sort(key=lambda r: (r["sequence"], r["complete"]))
    if len(records) == 2 and records[0]["sequence"] == records[1]["sequence"]:
        _require(_equal(records[0]["digest"], records[1]["digest"]),
                 "Conflicting antirollback records")
    return records[-1]


def _check_version(plan, nvm, trust):
    baseline = trust.MIN_SEQUENCE
    _require(type(baseline) is int and 0 <= baseline <= MAX_SEQUENCE,
             "Invalid provisioned version floor")
    current = _state(nvm)
    _require(current is not None,
             "Antirollback state blank; physical maintenance bootstrap required")
    sequence = plan["sequence"]
    if current is not None and sequence == current["sequence"]:
        _require(not current["complete"] and _equal(plan["digest"], current["digest"]),
                 "OTA replay or conflicting same-version package rejected")
        _require(sequence >= baseline, "OTA below provisioned version floor")
        return
    floor = max(baseline, current["sequence"] if current else 0)
    _require(sequence > floor, "OTA rollback/replay rejected")


def _verify_raw(source, entry, watchdog, destination=None):
    digest = _hash()
    size = 0
    crc = 0
    source.seek(entry["data_start"])
    while size < entry["uncomp_size"]:
        chunk = source.read(min(512, entry["uncomp_size"] - size))
        _require(bool(chunk), "Truncated STORED payload")
        size += len(chunk)
        digest.update(chunk)
        crc = binascii.crc32(chunk, crc)
        if destination is not None:
            destination.write(chunk)
        _feed(watchdog)
    _require(size == entry["uncomp_size"] and _equal(digest.digest(), entry["sha256"]),
             "Payload SHA-256/size mismatch: " + entry["name"])
    _require((crc & 0xFFFFFFFF) == entry["crc"], "ZIP CRC mismatch: " + entry["name"])


def extract_verified_entry(zip_path, entry, destination, watchdog=None):
    """Extract to staging ONLY; never pass a live executable destination."""
    with open(zip_path, "rb") as source:
        with open(destination, "wb") as output:
            _verify_raw(source, entry, watchdog, output)
    verify_staged_file(entry, destination, watchdog)


def verify_package(zip_path, entries=None, watchdog=None, nvm=None, trust=None):
    """Read-only verification. Returned entries are the ONLY installable files.

    ``entries`` is ignored for compatibility: independently parse the whole ZIP,
    so a legacy parser cannot hide duplicate, malformed or unsupported members.
    Tests inject nvm/trust; device callers must leave both at their defaults.
    """
    del entries
    trust = ota_trust if trust is None else trust
    _device_compatible()
    parsed = _zip_entries(zip_path)
    by_name = {entry["name"]: entry for entry in parsed}
    _require(MANIFEST_NAME in by_name and SIGNATURE_NAME in by_name,
             "Signed manifest and signature required; unsigned OTA disabled")
    with open(zip_path, "rb") as source:
        manifest = _member_bytes(source, by_name[MANIFEST_NAME], MAX_MANIFEST)
        signature = _member_bytes(source, by_name[SIGNATURE_NAME], 256)
        _verify_signature(manifest, signature, trust, watchdog)
        sequence, files = _manifest(manifest, trust)
        _require(len(parsed) == len(files) + 2, "Manifest/ZIP inventory mismatch")
        payload = []
        for entry in parsed:
            name = entry["name"]
            if name in (MANIFEST_NAME, SIGNATURE_NAME):
                continue
            _require(name in files and entry["uncomp_size"] == files[name][0],
                     "Manifest/ZIP file mismatch")
            entry["sha256"] = files[name][1]
            payload.append(entry)
        # Authenticate the complete inventory before reservation or installation.
        for entry in payload:
            _verify_raw(source, entry, watchdog)
    plan = {"sequence": sequence, "digest": _hash(manifest).digest(),
            "entries": payload, "test_only": bool(trust.TEST_ONLY)}
    _check_version(plan, _nvm_bytes(nvm), trust)
    return plan


def verify_staged_file(entry, path, watchdog=None):
    digest = _hash()
    size = 0
    with open(path, "rb") as source:
        while True:
            chunk = source.read(512)
            if not chunk:
                break
            size += len(chunk)
            _require(size <= entry["uncomp_size"], "Oversized staged file")
            digest.update(chunk)
            _feed(watchdog)
    _require(size == entry["uncomp_size"] and _equal(digest.digest(), entry["sha256"]),
             "Staged file SHA-256 mismatch")


def _write_state(plan, complete, nvm):
    prefix = (b"KOT1" + bytes([2 if complete else 1]) + b"\0\0\0" +
              struct.pack("<I", plan["sequence"]) + plan["digest"] + b"\0\0\0\0")
    raw = prefix + _hash(prefix).digest()
    for slot in range(2):
        start = NVM_OFFSET + slot * NVM_RECORD_SIZE
        if bytes(nvm[start:start + NVM_RECORD_SIZE]) != raw:
            nvm[start:start + NVM_RECORD_SIZE] = raw
        _require(bytes(nvm[start:start + NVM_RECORD_SIZE]) == raw,
                 "Antirollback state write failed")


def reserve_version(plan, nvm=None, trust=None):
    """Persist pending sequence BEFORE the first live executable change."""
    nvm = _nvm_bytes(nvm)
    _check_version(plan, nvm, ota_trust if trust is None else trust)
    _write_state(plan, False, nvm)


def complete_version(plan, nvm=None):
    """Call only AFTER all payload files were installed and verified."""
    nvm = _nvm_bytes(nvm)
    current = _state(nvm)
    _require(current and current["sequence"] == plan["sequence"] and
             _equal(current["digest"], plan["digest"]), "OTA reservation missing")
    _write_state(plan, True, nvm)


def initialize_baseline(nvm=None, trust=None):
    """Physical-maintenance boot ONLY: initialize virgin NVM, never reset it.

    Caller must require the physical maintenance button. Normal OTA handlers
    must never call this. Repeating it on an initialized device is a no-op.
    """
    trust = ota_trust if trust is None else trust
    _require(len(trust.PUBLIC_KEY_N_HEX) == 512, "OTA trust key not provisioned")
    _require(type(trust.MIN_SEQUENCE) is int and
             1 <= trust.MIN_SEQUENCE <= MAX_SEQUENCE, "Invalid bootstrap baseline")
    nvm = _nvm_bytes(nvm)
    current = _state(nvm)
    if current is not None:
        return False
    # _state() rejects both malformed records. Require both slots virgin even
    # if a future record decoder starts recognizing additional empty patterns.
    for slot in range(2):
        start = NVM_OFFSET + slot * NVM_RECORD_SIZE
        raw = bytes(nvm[start:start + NVM_RECORD_SIZE])
        _require(raw in (b"\0" * NVM_RECORD_SIZE, b"\xff" * NVM_RECORD_SIZE),
                 "Nonvirgin antirollback state; physical recovery required")
    _write_state({"sequence": trust.MIN_SEQUENCE, "digest": b"\0" * 32}, True, nvm)
    return True
