"""Local model settings. Secrets never appear in public settings or research archives."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from ..core.common import write_json

_lock = threading.RLock()
_session_keys = {}
DEFAULT = {"base_url": "https://api.openai.com/v1", "model": "", "max_rounds": 3,
           "max_output_tokens": 2200, "storage": "session", "api_style": "chat_completions"}


def settings_path(root):
    return Path(root) / "local/assistant/model.json"


def dpapi(value, decrypt=False):
    if os.name != "nt":
        raise ValueError("此系统请使用仅本次服务保存密钥")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError("Windows 密钥保护失败，请重新填写密钥或使用仅本次服务保存")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def validate(body):
    if not isinstance(body, dict) or set(body) - set(DEFAULT) - {"api_key", "clear_key"}:
        raise ValueError("模型设置字段无效")
    value = {**DEFAULT, **{k: v for k, v in body.items() if k in DEFAULT}}
    base = value["base_url"]
    if not isinstance(base, str) or len(base) > 500:
        raise ValueError("API 地址无效")
    parsed = urlsplit(base.strip())
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in {"http", "https"}):
        raise ValueError("API 地址须为完整 HTTP(S) 地址，不含账号、查询参数或片段")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程模型地址需使用 HTTPS；本机模型可以使用 HTTP")
    base = base.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    elif base.endswith("/responses"):
        base = base[:-len("/responses")]
    elif value["api_style"] == "anthropic" and base.endswith("/messages"):
        base = base[:-len("/messages")]
    value["base_url"] = base
    if value["api_style"] not in {"chat_completions", "responses", "anthropic"}:
        raise ValueError("接口格式无效")
    if not isinstance(value["model"], str) or not value["model"].strip() or len(value["model"]) > 150:
        raise ValueError("请填写服务商提供的模型名称")
    value["model"] = value["model"].strip()
    for field, low, high in (("max_rounds", 1, 5), ("max_output_tokens", 256, 8000)):
        if type(value[field]) is not int or not low <= value[field] <= high:
            raise ValueError("模型参数超出范围：" + field)
    if value["storage"] not in {"session", "protected"}:
        raise ValueError("请选择密钥保存方式")
    if "api_key" in body and (not isinstance(body["api_key"], str) or len(body["api_key"]) > 4096 or any(c in body["api_key"] for c in "\r\n")):
        raise ValueError("API 密钥格式无效")
    return value


def read(root, *, secret=False):
    with _lock:
        path = settings_path(root)
        stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        value = {**DEFAULT, **{k: stored[k] for k in DEFAULT if k in stored}}
        token = _session_keys.get(str(path.resolve()), "")
        error = None
        if stored.get("protected_key"):
            try:
                token = dpapi(base64.b64decode(stored["protected_key"]), decrypt=True).decode("utf-8")
            except Exception:
                error = "保存的密钥无法解密，请重新填写"
        local = urlsplit(value["base_url"]).hostname in {"localhost", "127.0.0.1", "::1"}
        value.update(has_key=bool(token), configured=bool(value["model"] and (token or local) and not error),
                     key_error=error, protected_storage_available=os.name == "nt")
        if secret:
            value["api_key"] = token
        return value


def save(root, body):
    with _lock:
        path = settings_path(root)
        original = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        prior = read(root, secret=True)
        value = validate({**{k: prior[k] for k in DEFAULT}, **body})
        token = body.get("api_key") or prior.get("api_key", "")
        if body.get("clear_key") is True:
            token = ""
        # Never forward a previous provider's credential to a newly entered endpoint.
        if value["base_url"] != prior["base_url"] and not body.get("api_key"):
            token = ""
        stored = dict(value)
        preserve_cipher = (original.get("protected_key") and value["storage"] == "protected"
                           and value["base_url"] == prior["base_url"]
                           and not body.get("api_key") and body.get("clear_key") is not True)
        if preserve_cipher:
            # A protocol/model edit does not need decryption or re-encryption.
            stored["protected_key"] = original["protected_key"]
        elif (prior.get("key_error") and value["base_url"] == prior["base_url"]
              and not body.get("api_key") and body.get("clear_key") is not True):
            raise ValueError("原密钥无法解密，已保留原配置；请重新输入密钥后再修改保存方式")
        elif token and value["storage"] == "protected":
            stored["protected_key"] = base64.b64encode(dpapi(token.encode())).decode()
        write_json(path, stored)
        _session_keys[str(path.resolve())] = token if value["storage"] == "session" else ""
        return read(root)
