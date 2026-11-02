"""Local model settings. Secrets never appear in public settings or research archives."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
import re
import ipaddress
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlsplit

from ..core.common import write_json

_lock = threading.RLock()
_session_keys = {}
DEFAULT = {"base_url": "https://api.openai.com/v1", "model": "", "max_rounds": 3,
           "max_output_tokens": 2200, "storage": "protected" if os.name == "nt" else "session", "api_style": "chat_completions",
           "allow_insecure_http": False, "responses_mode": "standard", "reasoning_effort": ""}


def settings_path(root):
    return Path(root) / "local/assistant/model-profiles.json"


def legacy_path(root):
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
    if type(value["allow_insecure_http"]) is not bool:
        raise ValueError("内网 HTTP 选项无效")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        try: private = ipaddress.ip_address(parsed.hostname).is_private
        except ValueError: private = False
        if not (private and value["allow_insecure_http"]):
            raise ValueError("远程模型需 HTTPS；可信内网 IP 可显式启用内网 HTTP")
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
    if value["responses_mode"] not in {"standard", "codex"}:
        raise ValueError("Responses 模式无效")
    if value["reasoning_effort"] not in {"", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
        raise ValueError("推理强度无效")
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


def _store(root):
    path = settings_path(root)
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("profiles"), dict):
            raise ValueError("模型配置列表格式无效，原文件已保留")
        if any(not isinstance(p, dict) for p in value["profiles"].values()): raise ValueError("模型配置条目格式无效")
        if value.get("active_id") not in value["profiles"] and value["profiles"]:
            raise ValueError("当前模型配置不存在")
        return value
    old = legacy_path(root)
    profiles = {}
    if old.exists():
        raw = json.loads(old.read_text(encoding="utf-8"))
        if raw.get("model"):
            name = "Kimi Code" if "kimi" in (urlsplit(raw.get("base_url", "")).hostname or "") else "原有配置"
            profiles["legacy"] = {**raw, "name": name}
    return {"schema_version": 1, "active_id": "legacy" if profiles else None, "profiles": profiles}


def _key_id(root, identifier):
    return str(settings_path(root).resolve()) + "#" + str(identifier)


def _value(root, identifier, stored, secret=False):
    value = {**DEFAULT, **{k: stored[k] for k in DEFAULT if k in stored}}
    token = _session_keys.get(_key_id(root, identifier), "")
    error = None
    if stored.get("protected_key"):
        try: token = dpapi(base64.b64decode(stored["protected_key"]), decrypt=True).decode("utf-8")
        except Exception: error = "保存的密钥无法解密，原密文保留，请重新填写"
    local = urlsplit(value["base_url"]).hostname in {"localhost", "127.0.0.1", "::1"}
    value.update(profile_id=identifier, profile_name=stored.get("name", ""), has_key=bool(token),
        configured=bool(value["model"] and (token or local) and not error), key_error=error,
        protected_storage_available=os.name == "nt")
    if secret: value["api_key"] = token
    return value


def read(root, *, secret=False):
    with _lock:
        store = _store(root); active = store["active_id"]
        value = _value(root, active, store["profiles"].get(active, {}), secret)
        value["profiles"] = [{"id": identifier, "name": p.get("name", p.get("model", "未命名")),
                              "model": p.get("model"), "storage": p.get("storage", "session")}
                             for identifier, p in store["profiles"].items()]
        return value


def save(root, body):
    with _lock:
        if not isinstance(body, dict) or set(body) - set(DEFAULT) - {"api_key", "clear_key", "profile_id", "profile_name", "new_profile"}:
            raise ValueError("模型配置字段无效")
        store = _store(root)
        creating = body.get("new_profile") is True or not store["profiles"]
        identifier = uuid4().hex if creating else body.get("profile_id", store["active_id"])
        if not creating and (not isinstance(identifier, str) or identifier not in store["profiles"]): raise ValueError("所选配置不存在")
        if creating and len(store["profiles"]) >= 30: raise ValueError("最多保存30个模型配置")
        original = {} if creating else store["profiles"][identifier]
        prior = _value(root, identifier, original, secret=True)
        value = validate({**{k: prior[k] for k in DEFAULT}, **{k: v for k, v in body.items() if k in DEFAULT or k in {"api_key", "clear_key"}}})
        name = body.get("profile_name", original.get("name") or value["model"])
        if not isinstance(name, str) or not name.strip() or len(name) > 80: raise ValueError("请填写不超过80字的配置名称")
        token = body.get("api_key") or prior.get("api_key", "")
        if body.get("clear_key") is True or (value["base_url"] != prior["base_url"] and not body.get("api_key")): token = ""
        stored = {**value, "name": name.strip()}
        preserve = (original.get("protected_key") and value["storage"] == "protected" and value["base_url"] == prior["base_url"]
                    and not body.get("api_key") and body.get("clear_key") is not True)
        if preserve: stored["protected_key"] = original["protected_key"]
        elif prior.get("key_error") and value["base_url"] == prior["base_url"] and not body.get("api_key") and body.get("clear_key") is not True:
            raise ValueError("原密钥无法解密，已保留原配置；请重新输入密钥后再修改保存方式")
        elif token and value["storage"] == "protected": stored["protected_key"] = base64.b64encode(dpapi(token.encode())).decode()
        store["profiles"][identifier] = stored; store["active_id"] = identifier
        write_json(settings_path(root), store)
        _session_keys[_key_id(root, identifier)] = token if value["storage"] == "session" else ""
        return read(root)


def select(root, identifier):
    with _lock:
        store = _store(root)
        if not isinstance(identifier, str) or identifier not in store["profiles"]: raise ValueError("所选配置不存在")
        store["active_id"] = identifier
        write_json(settings_path(root), store)
        return read(root)


def import_codex(root):
    """Explicitly import only icrc; never expose or copy the entire Codex configuration."""
    from ..agents.runtime import load_toml
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    base = load_toml(home / "config.toml")
    profile = base.get("profiles", {}).get("icrc", {})
    effective = {**base, **profile}
    if effective.get("model_provider") != "icrc":
        extra = home / "icrc.config.toml"
        if not extra.is_file(): raise ValueError("Codex 中没有找到 icrc 配置")
        effective = {**base, **load_toml(extra)}
    if effective.get("model_provider") != "icrc": raise ValueError("配置没有指向 icrc")
    relay = {**base.get("model_providers", {}), **effective.get("model_providers", {})}.get("icrc", {})
    if relay.get("wire_api", "responses") != "responses": raise ValueError("icrc 接口格式不是 Responses，请手动配置")
    token = None
    env_name = relay.get("env_key")
    if env_name and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name): token = os.environ.get(env_name)
    token = token or relay.get("experimental_bearer_token") or relay.get("api_key")
    if not token and relay.get("requires_openai_auth"):
        auth_path = home / "auth.json"
        if auth_path.exists(): token = json.loads(auth_path.read_text(encoding="utf-8-sig")).get("OPENAI_API_KEY")
    if not isinstance(token, str) or not token: raise ValueError("未找到可用的 icrc API Key；不会导入 OAuth 登录凭据")
    url = relay.get("base_url", "")
    parsed = urlsplit(url)
    body = {"profile_name": "icrc · Codex", "base_url": url, "model": effective.get("model", ""),
            "api_style": "responses", "responses_mode": "codex" if parsed.path.rstrip('/').endswith('/backend-api/codex') else "standard",
            "reasoning_effort": effective.get("model_reasoning_effort", ""),
            "allow_insecure_http": parsed.scheme == "http", "storage": "protected" if os.name == "nt" else "session", "api_key": token}
    with _lock:
        store = _store(root)
        existing = next((identifier for identifier, p in store["profiles"].items() if p.get("name") == body["profile_name"] and p.get("base_url") == url), None)
        if existing: body["profile_id"] = existing
        else: body["new_profile"] = True
        return save(root, body)
