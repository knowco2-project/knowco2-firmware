#!/usr/bin/env python3
"""Validate and export the locked KnowCO2 CircuitPython build dependency tuple.

The firmware repository is the source of truth for the board runtime, Adafruit
bundle, vendored ``lib`` snapshot, and ``mpy-cross`` compiler.  This tool is
intentionally standard-library-only so it can run in GitHub Actions, CodeBuild,
and a developer checkout without installing another package manager.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_PATH = REPOSITORY_ROOT / "dependencies" / "firmware-build-lock.json"
LOCK_SCHEMA = "knowco2.firmware.dependencies.v1"
GITHUB_API_VERSION = "2022-11-28"
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
BUNDLE_TAG = re.compile(r"^[0-9]{8}$")
RELEASE_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z._-]*$")
SAFE_ENV_VALUE = re.compile(r"^[^\r\n]*$")


class DependencyPolicyError(RuntimeError):
    """Raised when the locked firmware dependency policy is invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DependencyPolicyError(message)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{name} must be a JSON object")
    return value


def _string(value: Any, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{name} must be a non-empty string")
    return value.strip()


def _integer(value: Any, name: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{name} must be an integer")
    return value


def _version_tuple(value: str, name: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", value)
    _require(match is not None, f"{name} must be a three-part semantic version")
    assert match is not None
    return tuple(int(part) for part in match.groups())


def _safe_relative_path(value: str, name: str) -> Path:
    path = Path(value)
    _require(not path.is_absolute(), f"{name} must be repository-relative")
    _require(".." not in path.parts, f"{name} may not contain '..'")
    _require(bool(path.parts), f"{name} may not be empty")
    return path


def load_lock(path: Path = DEFAULT_LOCK_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DependencyPolicyError(f"Could not read dependency lock {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DependencyPolicyError(f"Dependency lock is not valid JSON: {exc}") from exc
    _require(isinstance(value, dict), "Dependency lock root must be a JSON object")
    return value


def calculated_library_set_id(lock: Mapping[str, Any]) -> str:
    circuitpython = _mapping(lock.get("circuitpython"), "circuitpython")
    bundle = _mapping(lock.get("library_bundle"), "library_bundle")
    cp_version = _string(circuitpython.get("version"), "circuitpython.version")
    tag = _string(bundle.get("tag"), "library_bundle.tag")
    tree_sha = _string(bundle.get("vendored_git_tree_sha"), "library_bundle.vendored_git_tree_sha")
    _require(HEX_40.fullmatch(tree_sha) is not None, "library_bundle.vendored_git_tree_sha must be a lowercase Git SHA")
    return f"cp{cp_version}-bundle{tag}-{tree_sha[:12]}"


def validate_lock(lock: Mapping[str, Any]) -> None:
    _require(lock.get("schema") == LOCK_SCHEMA, f"schema must be {LOCK_SCHEMA}")
    _string(lock.get("hardware_profile"), "hardware_profile")
    _mapping(lock.get("approval"), "approval")

    board = _mapping(lock.get("board"), "board")
    board_id = _string(board.get("id"), "board.id")
    _require(re.fullmatch(r"[a-z0-9_]+", board_id) is not None, "board.id contains unsupported characters")

    circuitpython = _mapping(lock.get("circuitpython"), "circuitpython")
    cp_version = _string(circuitpython.get("version"), "circuitpython.version")
    cp_tuple = _version_tuple(cp_version, "circuitpython.version")
    cp_major = _integer(circuitpython.get("major"), "circuitpython.major")
    _require(cp_tuple[0] == cp_major, "circuitpython.major does not match circuitpython.version")
    minimum = _version_tuple(
        _string(circuitpython.get("minimum_supported"), "circuitpython.minimum_supported"),
        "circuitpython.minimum_supported",
    )
    maximum = _version_tuple(
        _string(circuitpython.get("maximum_exclusive"), "circuitpython.maximum_exclusive"),
        "circuitpython.maximum_exclusive",
    )
    _require(minimum <= cp_tuple < maximum, "locked CircuitPython version is outside its declared support range")
    _safe_relative_path(
        _string(circuitpython.get("checked_in_path"), "circuitpython.checked_in_path"),
        "circuitpython.checked_in_path",
    )
    cp_blob = _string(circuitpython.get("checked_in_git_blob_sha"), "circuitpython.checked_in_git_blob_sha")
    _require(HEX_40.fullmatch(cp_blob) is not None, "circuitpython.checked_in_git_blob_sha must be a lowercase Git SHA")
    download = urllib.parse.urlparse(_string(circuitpython.get("download_url"), "circuitpython.download_url"))
    _require(download.scheme == "https", "circuitpython.download_url must use HTTPS")

    bundle = _mapping(lock.get("library_bundle"), "library_bundle")
    tag = _string(bundle.get("tag"), "library_bundle.tag")
    _require(BUNDLE_TAG.fullmatch(tag) is not None, "library_bundle.tag must be YYYYMMDD")
    _require(_integer(bundle.get("major"), "library_bundle.major") == cp_major, "bundle major must match CircuitPython major")
    _safe_relative_path(_string(bundle.get("vendored_path"), "library_bundle.vendored_path"), "library_bundle.vendored_path")
    tree_sha = _string(bundle.get("vendored_git_tree_sha"), "library_bundle.vendored_git_tree_sha")
    _require(HEX_40.fullmatch(tree_sha) is not None, "library_bundle.vendored_git_tree_sha must be a lowercase Git SHA")
    expected_top_level = bundle.get("expected_top_level")
    _require(isinstance(expected_top_level, list) and bool(expected_top_level), "library_bundle.expected_top_level must be a non-empty list")
    normalized_top_level = [_string(item, "library_bundle.expected_top_level item") for item in expected_top_level]
    _require(len(normalized_top_level) == len(set(normalized_top_level)), "library_bundle.expected_top_level contains duplicates")
    _require(
        all(Path(item).name == item and item not in {".", ".."} for item in normalized_top_level),
        "library_bundle.expected_top_level entries must be direct child names",
    )
    release_commit = _string(bundle.get("release_commit"), "library_bundle.release_commit")
    _require(HEX_40.fullmatch(release_commit) is not None, "library_bundle.release_commit must be a lowercase Git SHA")
    for asset_name in ("source_asset", "precompiled_asset"):
        asset = _mapping(bundle.get(asset_name), f"library_bundle.{asset_name}")
        filename = _string(asset.get("filename"), f"library_bundle.{asset_name}.filename")
        url = urllib.parse.urlparse(_string(asset.get("url"), f"library_bundle.{asset_name}.url"))
        digest = _string(asset.get("sha256"), f"library_bundle.{asset_name}.sha256")
        _require(url.scheme == "https" and url.path.endswith(filename), f"library_bundle.{asset_name}.url does not match its filename")
        _require(SHA256.fullmatch(digest) is not None, f"library_bundle.{asset_name}.sha256 must be lowercase SHA-256")
    _require(
        _string(bundle.get("library_set_id"), "library_bundle.library_set_id") == calculated_library_set_id(lock),
        "library_bundle.library_set_id does not match the locked runtime, bundle, and vendored tree",
    )

    compiler = _mapping(lock.get("mpy_cross"), "mpy_cross")
    _require(_string(compiler.get("version"), "mpy_cross.version") == cp_version, "mpy-cross must exactly match CircuitPython")
    _require(_string(compiler.get("source_ref"), "mpy_cross.source_ref") == cp_version, "mpy_cross.source_ref must match CircuitPython")
    for key in ("source_commit", "huffman_commit"):
        value = _string(compiler.get(key), f"mpy_cross.{key}")
        _require(HEX_40.fullmatch(value) is not None, f"mpy_cross.{key} must be a lowercase Git SHA")
    for key in ("source_repository", "huffman_repository"):
        parsed = urllib.parse.urlparse(_string(compiler.get(key), f"mpy_cross.{key}"))
        _require(parsed.scheme == "https" and parsed.hostname == "github.com", f"mpy_cross.{key} must be an HTTPS GitHub URL")
    header = compiler.get("expected_mpy_header")
    _require(isinstance(header, list) and len(header) == 4, "mpy_cross.expected_mpy_header must contain four bytes")
    _require(all(isinstance(item, int) and 0 <= item <= 255 for item in header), "mpy_cross.expected_mpy_header contains an invalid byte")
    _require(header[0] == ord("C"), "CircuitPython .mpy files must use magic byte 'C'")

    release = _mapping(lock.get("release"), "release")
    _require(release.get("compatibility_schema") == "knowco2.firmware.compatibility.v1", "unsupported compatibility schema")
    _require(release.get("build_manifest_schema") == "knowco2.firmware.build.v1", "unsupported build manifest schema")
    majors = release.get("supported_major_versions")
    _require(isinstance(majors, list) and cp_major in majors, "release.supported_major_versions must include the locked major")
    tested = release.get("production_tested")
    _require(isinstance(tested, list) and cp_version in tested, "release.production_tested must include the locked CircuitPython version")
    for key in ("source_code_asset_template", "source_asset_template", "full_asset_template", "ota_asset_template"):
        template = _string(release.get(key), f"release.{key}")
        _require("{release}" in template, f"release.{key} must contain {{release}}")

    monitor = _mapping(lock.get("monitor"), "monitor")
    monitor_url = urllib.parse.urlparse(_string(monitor.get("release_api"), "monitor.release_api"))
    _require(monitor_url.scheme == "https" and monitor_url.hostname == "api.github.com", "monitor.release_api must be the GitHub HTTPS API")
    _string(monitor.get("issue_title"), "monitor.issue_title")


def _git_object_sha(kind: str, content: bytes) -> str:
    header = f"{kind} {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _git_blob_sha(path: Path) -> str:
    try:
        if path.is_symlink():
            content = os.readlink(path).encode("utf-8")
        else:
            content = path.read_bytes()
    except OSError as exc:
        raise DependencyPolicyError(f"Could not hash {path}: {exc}") from exc
    return _git_object_sha("blob", content)


def _git_tree_sha(directory: Path) -> str:
    """Calculate the Git tree ID for the current filesystem contents.

    Computing the object ID directly catches staged, unstaged, and untracked
    changes and also works in source archives where a ``.git`` directory is not
    available. Git sorts tree names as bytes and treats a directory as if its
    name had a trailing slash.
    """
    entries: list[tuple[bytes, bytes, bytes, bytes]] = []
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise DependencyPolicyError(f"Could not inspect {directory}: {exc}") from exc

    for child in children:
        try:
            status = child.lstat()
        except OSError as exc:
            raise DependencyPolicyError(f"Could not inspect {child}: {exc}") from exc
        name = os.fsencode(child.name)
        if stat.S_ISDIR(status.st_mode):
            mode = b"40000"
            object_sha = _git_tree_sha(child)
            sort_key = name + b"/"
        elif stat.S_ISLNK(status.st_mode):
            mode = b"120000"
            object_sha = _git_blob_sha(child)
            sort_key = name
        elif stat.S_ISREG(status.st_mode):
            mode = b"100755" if status.st_mode & 0o111 else b"100644"
            object_sha = _git_blob_sha(child)
            sort_key = name
        else:
            raise DependencyPolicyError(f"Unsupported filesystem entry in dependency snapshot: {child}")
        entries.append((sort_key, mode, name, bytes.fromhex(object_sha)))

    entries.sort(key=lambda item: item[0])
    content = b"".join(mode + b" " + name + b"\0" + object_id for _, mode, name, object_id in entries)
    return _git_object_sha("tree", content)


def verify_repository(repository: Path, lock: Mapping[str, Any]) -> list[str]:
    validate_lock(lock)
    repository = repository.resolve()
    messages: list[str] = []

    circuitpython = _mapping(lock["circuitpython"], "circuitpython")
    cp_relative = _safe_relative_path(_string(circuitpython["checked_in_path"], "circuitpython.checked_in_path"), "circuitpython.checked_in_path")
    cp_path = repository / cp_relative
    _require(cp_path.is_file(), f"checked-in CircuitPython UF2 is missing: {cp_relative.as_posix()}")
    cp_blob = _git_blob_sha(cp_path)
    _require(cp_blob == circuitpython["checked_in_git_blob_sha"], "checked-in CircuitPython UF2 no longer matches the lock")
    messages.append(f"CircuitPython {circuitpython['version']} UF2: {cp_blob}")

    bundle = _mapping(lock["library_bundle"], "library_bundle")
    lib_relative = _safe_relative_path(_string(bundle["vendored_path"], "library_bundle.vendored_path"), "library_bundle.vendored_path")
    lib_path = repository / lib_relative
    _require(lib_path.is_dir(), f"vendored library directory is missing: {lib_relative.as_posix()}")
    current_tree = _git_tree_sha(lib_path)
    _require(current_tree == bundle["vendored_git_tree_sha"], "vendored library Git tree no longer matches the lock")

    expected_top = sorted(str(item) for item in bundle["expected_top_level"])
    actual_top = sorted(item.name for item in lib_path.iterdir())
    _require(actual_top == expected_top, f"vendored top-level inventory differs from lock; expected {expected_top}, found {actual_top}")

    forbidden = [
        path.relative_to(repository).as_posix()
        for path in lib_path.rglob("*")
        if path.is_symlink()
        or path.name == ".DS_Store"
        or path.name == "__pycache__"
        or path.suffix == ".pyc"
    ]
    _require(not forbidden, "vendored library directory contains forbidden generated files or symlinks: " + ", ".join(forbidden))

    compiler = _mapping(lock["mpy_cross"], "mpy_cross")
    expected_header = bytes(int(item) for item in compiler["expected_mpy_header"])
    mpy_files = sorted(lib_path.rglob("*.mpy"))
    _require(bool(mpy_files), "vendored library directory contains no .mpy files")
    wrong_headers: list[str] = []
    for path in mpy_files:
        try:
            actual_header = path.read_bytes()[:4]
        except OSError as exc:
            raise DependencyPolicyError(f"Could not read {path}: {exc}") from exc
        if actual_header != expected_header:
            wrong_headers.append(path.relative_to(repository).as_posix())
    _require(not wrong_headers, "vendored .mpy ABI header mismatch: " + ", ".join(wrong_headers))
    messages.append(f"Vendored bundle {bundle['tag']}: {len(mpy_files)} .mpy files, tree {current_tree}")

    notices_path = repository / "THIRD_PARTY_NOTICES.md"
    _require(notices_path.is_file(), "THIRD_PARTY_NOTICES.md is missing")
    notices = notices_path.read_text(encoding="utf-8")
    _require(lib_relative.as_posix() in notices, "THIRD_PARTY_NOTICES.md does not identify the locked vendored directory")
    missing_notices = []
    for item in expected_top:
        component = item[:-4] if item.endswith(".mpy") else item
        if f"`{component}`" not in notices:
            missing_notices.append(component)
    _require(not missing_notices, "THIRD_PARTY_NOTICES.md is missing components: " + ", ".join(missing_notices))
    messages.append(f"Library set ID: {bundle['library_set_id']}")
    return messages


def _release_tag(value: str) -> str:
    value = value.strip()
    _require(RELEASE_TAG.fullmatch(value) is not None, "release tag must look like v1.2.3")
    return value


def build_compatibility_manifest(lock: Mapping[str, Any], release_tag: str) -> dict[str, Any]:
    validate_lock(lock)
    release_tag = _release_tag(release_tag)
    board = _mapping(lock["board"], "board")
    circuitpython = _mapping(lock["circuitpython"], "circuitpython")
    bundle = _mapping(lock["library_bundle"], "library_bundle")
    compiler = _mapping(lock["mpy_cross"], "mpy_cross")
    release = _mapping(lock["release"], "release")
    return {
        "schema": release["compatibility_schema"],
        "firmware_release": release_tag,
        "board_ids": [board["id"]],
        "circuitpython": {
            "supported_major_versions": list(release["supported_major_versions"]),
            "minimum": circuitpython["minimum_supported"],
            "maximum_exclusive": circuitpython["maximum_exclusive"],
            "production_tested": list(release["production_tested"]),
        },
        "source_asset": str(release["source_asset_template"]).format(release=release_tag),
        "full_asset": str(release["full_asset_template"]).format(release=release_tag),
        "ota_asset": str(release["ota_asset_template"]).format(release=release_tag),
        "build_manifest": f"knowco2-build-{release_tag}.json",
        "dependency_lock": {
            "schema": lock["schema"],
            "hardware_profile": lock["hardware_profile"],
            "library_set_id": bundle["library_set_id"],
            "bundle_tag": bundle["tag"],
            "bundle_release_commit": bundle["release_commit"],
            "vendored_git_tree_sha": bundle["vendored_git_tree_sha"],
            "mpy_cross_version": compiler["version"],
        },
    }


def build_release_manifest(
    lock: Mapping[str, Any],
    release_tag: str,
    *,
    firmware_version: str = "unknown",
) -> dict[str, Any]:
    validate_lock(lock)
    release_tag = _release_tag(release_tag)
    board = _mapping(lock["board"], "board")
    circuitpython = _mapping(lock["circuitpython"], "circuitpython")
    bundle = _mapping(lock["library_bundle"], "library_bundle")
    compiler = _mapping(lock["mpy_cross"], "mpy_cross")
    release = _mapping(lock["release"], "release")
    return {
        "schema": release["build_manifest_schema"],
        "firmware_release": release_tag,
        "firmware_version": firmware_version,
        "hardware_profile": lock["hardware_profile"],
        "board": copy.deepcopy(dict(board)),
        "circuitpython": {
            "version": circuitpython["version"],
            "major": circuitpython["major"],
            "checked_in_path": circuitpython["checked_in_path"],
            "checked_in_git_blob_sha": circuitpython["checked_in_git_blob_sha"],
            "download_url": circuitpython["download_url"],
        },
        "library_bundle": {
            "tag": bundle["tag"],
            "major": bundle["major"],
            "release_commit": bundle["release_commit"],
            "library_set_id": bundle["library_set_id"],
            "vendored_path": bundle["vendored_path"],
            "vendored_git_tree_sha": bundle["vendored_git_tree_sha"],
            "source_asset": copy.deepcopy(dict(bundle["source_asset"])),
            "precompiled_asset": copy.deepcopy(dict(bundle["precompiled_asset"])),
        },
        "mpy_cross": {
            "version": compiler["version"],
            "source_repository": compiler["source_repository"],
            "source_ref": compiler["source_ref"],
            "source_commit": compiler["source_commit"],
            "huffman_repository": compiler["huffman_repository"],
            "huffman_commit": compiler["huffman_commit"],
            "expected_mpy_header": list(compiler["expected_mpy_header"]),
        },
        "artifacts": {
            "source_code": str(release["source_code_asset_template"]).format(release=release_tag),
            "development": str(release["source_asset_template"]).format(release=release_tag),
            "full": str(release["full_asset_template"]).format(release=release_tag),
            "ota": str(release["ota_asset_template"]).format(release=release_tag),
            "compatibility": f"knowco2-compatibility-{release_tag}.json",
        },
        "approval": copy.deepcopy(dict(lock["approval"])),
    }


def environment_values(lock: Mapping[str, Any]) -> dict[str, str]:
    validate_lock(lock)
    board = _mapping(lock["board"], "board")
    circuitpython = _mapping(lock["circuitpython"], "circuitpython")
    bundle = _mapping(lock["library_bundle"], "library_bundle")
    compiler = _mapping(lock["mpy_cross"], "mpy_cross")
    return {
        "KNOWCO2_BOARD_ID": str(board["id"]),
        "KNOWCO2_CP_VERSION": str(circuitpython["version"]),
        "KNOWCO2_CP_MAJOR": str(circuitpython["major"]),
        "KNOWCO2_CP_UF2": str(circuitpython["checked_in_path"]),
        "KNOWCO2_CP_UF2_GIT_BLOB_SHA": str(circuitpython["checked_in_git_blob_sha"]),
        "KNOWCO2_LIB_DIR": str(bundle["vendored_path"]),
        "KNOWCO2_LIB_TREE_SHA": str(bundle["vendored_git_tree_sha"]),
        "KNOWCO2_LIBRARY_SET_ID": str(bundle["library_set_id"]),
        "KNOWCO2_BUNDLE_TAG": str(bundle["tag"]),
        "KNOWCO2_BUNDLE_RELEASE_COMMIT": str(bundle["release_commit"]),
        "KNOWCO2_BUNDLE_SOURCE_SHA256": str(bundle["source_asset"]["sha256"]),
        "KNOWCO2_BUNDLE_MPY_SHA256": str(bundle["precompiled_asset"]["sha256"]),
        "KNOWCO2_MPY_CROSS_VERSION": str(compiler["version"]),
        "KNOWCO2_MPY_CROSS_SOURCE_REPOSITORY": str(compiler["source_repository"]),
        "KNOWCO2_MPY_CROSS_SOURCE_REF": str(compiler["source_ref"]),
        "KNOWCO2_MPY_CROSS_SOURCE_COMMIT": str(compiler["source_commit"]),
        "KNOWCO2_HUFFMAN_REPOSITORY": str(compiler["huffman_repository"]),
        "KNOWCO2_HUFFMAN_COMMIT": str(compiler["huffman_commit"]),
    }


def _dotted_get(value: Any, dotted_path: str) -> Any:
    current = value
    for part in dotted_path.split("."):
        _require(isinstance(current, Mapping) and part in current, f"unknown lock field: {dotted_path}")
        current = current[part]
    return current


def parse_upstream_release(lock: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    validate_lock(lock)
    _require(isinstance(payload, Mapping), "upstream release response must be a JSON object")
    latest_tag = _string(payload.get("tag_name"), "upstream tag_name")
    _require(BUNDLE_TAG.fullmatch(latest_tag) is not None, "upstream bundle tag is not YYYYMMDD")
    bundle = _mapping(lock["library_bundle"], "library_bundle")
    current_tag = str(bundle["tag"])
    cp_major = int(bundle["major"])
    assets = payload.get("assets")
    _require(isinstance(assets, list), "upstream release assets must be a list")
    expected = {
        "source": f"adafruit-circuitpython-bundle-py-{latest_tag}.zip",
        "precompiled": f"adafruit-circuitpython-bundle-{cp_major}.x-mpy-{latest_tag}.zip",
    }
    resolved: dict[str, dict[str, str]] = {}
    for kind, filename in expected.items():
        matches = [item for item in assets if isinstance(item, Mapping) and item.get("name") == filename]
        _require(len(matches) == 1, f"upstream release must contain exactly one {filename}")
        item = matches[0]
        digest = _string(item.get("digest"), f"upstream {kind} digest")
        _require(digest.startswith("sha256:") and SHA256.fullmatch(digest[7:]) is not None, f"upstream {kind} asset has no valid SHA-256 digest")
        url = _string(item.get("browser_download_url"), f"upstream {kind} URL")
        parsed = urllib.parse.urlparse(url)
        _require(parsed.scheme == "https" and parsed.hostname == "github.com", f"upstream {kind} asset URL must be on GitHub")
        resolved[kind] = {"filename": filename, "url": url, "sha256": digest[7:]}
    release_commit = _string(payload.get("target_commitish"), "upstream target_commitish")
    _require(HEX_40.fullmatch(release_commit) is not None, "upstream bundle release must identify an immutable commit")
    return {
        "current_tag": current_tag,
        "latest_tag": latest_tag,
        "release_commit": release_commit,
        "update_available": int(latest_tag) > int(current_tag),
        "source_asset": resolved["source"],
        "precompiled_asset": resolved["precompiled"],
        "release_url": _string(payload.get("html_url"), "upstream html_url"),
    }


def fetch_upstream_release(lock: Mapping[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
    validate_lock(lock)
    monitor = _mapping(lock["monitor"], "monitor")
    url = _string(monitor["release_api"], "monitor.release_api")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "KnowCO2-Firmware/dependency-monitor",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = urllib.parse.urlparse(response.geturl())
            _require(final.scheme == "https" and final.hostname == "api.github.com", "dependency metadata redirected to an unapproved host")
            raw = response.read(4 * 1024 * 1024 + 1)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise DependencyPolicyError(f"Could not query the official Adafruit bundle release: {exc}") from exc
    _require(len(raw) <= 4 * 1024 * 1024, "dependency metadata response is unexpectedly large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DependencyPolicyError(f"Official Adafruit release response is invalid JSON: {exc}") from exc
    return parse_upstream_release(lock, payload)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_github_pairs(path: Path, pairs: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            text = str(value).lower() if isinstance(value, bool) else str(value)
            _require(SAFE_ENV_VALUE.fullmatch(text) is not None, f"unsafe newline in GitHub output {key}")
            handle.write(f"{key}={text}\n")


def _command_verify(args: argparse.Namespace) -> int:
    lock_path = Path(args.lock).resolve()
    lock = load_lock(lock_path)
    messages = verify_repository(Path(args.repository), lock)
    for message in messages:
        print(f"OK: {message}")
    return 0


def _command_get(args: argparse.Namespace) -> int:
    lock = load_lock(Path(args.lock))
    validate_lock(lock)
    value = _dotted_get(lock, args.field)
    print(json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value)
    return 0


def _command_export_shell(args: argparse.Namespace) -> int:
    values = environment_values(load_lock(Path(args.lock)))
    for key, value in values.items():
        print(f"{key}={shlex.quote(value)}")
    return 0


def _command_github_env(args: argparse.Namespace) -> int:
    values = environment_values(load_lock(Path(args.lock)))
    _write_github_pairs(Path(args.output), values)
    return 0


def _command_compatibility(args: argparse.Namespace) -> int:
    lock = load_lock(Path(args.lock))
    manifest = build_compatibility_manifest(lock, args.release_tag)
    _write_json(Path(args.output), manifest)
    print(Path(args.output))
    return 0


def _command_build_manifest(args: argparse.Namespace) -> int:
    lock = load_lock(Path(args.lock))
    manifest = build_release_manifest(lock, args.release_tag, firmware_version=args.firmware_version)
    _write_json(Path(args.output), manifest)
    print(Path(args.output))
    return 0


def _command_check_upstream(args: argparse.Namespace) -> int:
    lock = load_lock(Path(args.lock))
    report = fetch_upstream_release(lock, timeout=args.timeout)
    if args.json_output:
        _write_json(Path(args.json_output), report)
    if args.github_output:
        _write_github_pairs(
            Path(args.github_output),
            {
                "current_tag": report["current_tag"],
                "latest_tag": report["latest_tag"],
                "update_available": report["update_available"],
                "release_url": report["release_url"],
                "release_commit": report["release_commit"],
                "source_filename": report["source_asset"]["filename"],
                "source_sha256": report["source_asset"]["sha256"],
                "precompiled_filename": report["precompiled_asset"]["filename"],
                "precompiled_sha256": report["precompiled_asset"]["sha256"],
            },
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 3 if args.fail_on_update and report["update_available"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default=str(DEFAULT_LOCK_PATH), help="dependency lock JSON path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="verify the lock and checked-in dependency snapshot")
    verify.add_argument("--repository", default=str(REPOSITORY_ROOT))
    verify.set_defaults(func=_command_verify)

    get = subparsers.add_parser("get", help="print one dotted lock field")
    get.add_argument("field")
    get.set_defaults(func=_command_get)

    export_shell = subparsers.add_parser("export-shell", help="print shell-safe build environment assignments")
    export_shell.set_defaults(func=_command_export_shell)

    github_env = subparsers.add_parser("github-env", help="append build environment values to a GitHub environment file")
    github_env.add_argument("--output", required=True)
    github_env.set_defaults(func=_command_github_env)

    compatibility = subparsers.add_parser("compatibility", help="write a release compatibility manifest")
    compatibility.add_argument("--release-tag", required=True)
    compatibility.add_argument("--output", required=True)
    compatibility.set_defaults(func=_command_compatibility)

    build_manifest = subparsers.add_parser("build-manifest", help="write a traceable release build manifest")
    build_manifest.add_argument("--release-tag", required=True)
    build_manifest.add_argument("--firmware-version", default="unknown")
    build_manifest.add_argument("--output", required=True)
    build_manifest.set_defaults(func=_command_build_manifest)

    upstream = subparsers.add_parser("check-upstream", help="compare the lock with the latest official Adafruit bundle")
    upstream.add_argument("--timeout", type=float, default=20.0)
    upstream.add_argument("--json-output")
    upstream.add_argument("--github-output")
    upstream.add_argument("--fail-on-update", action="store_true")
    upstream.set_defaults(func=_command_check_upstream)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except DependencyPolicyError as exc:
        print(f"dependency policy error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
