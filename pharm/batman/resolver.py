"""Local-first BATMAN lookup with an official-download cache fallback."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

from . import local
from ..core.common import ROOT, now, read_json, write_json


OFFICIAL_URL = local.BATMAN_SOURCE_URL.rstrip("/") + "/"
CACHE_DIR = ROOT / "local/batman/official-download-cache"
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_CACHE_LOCK = threading.Lock()
HEADERS = {
    "herbs": {"Chinese.Name", "Ingredients"},
    "known_by_ingredients": {"PubChem_CID", "known_target_proteins"},
    "known_by_targets": {"entrez_gene_id", "entrez_gene_symbol"},
}


class DownloadLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = [dict(attrs).get("href", ""), ""]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append(tuple(self.current))
            self.current = None


def _official_url(url):
    if not isinstance(url, str) or not url.strip():
        raise RuntimeError("BATMAN 官方下载地址必须是非空字符串")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != urlparse(OFFICIAL_URL).hostname:
        raise RuntimeError("BATMAN 下载地址必须属于已配置的官方站点，不接受镜像或其他数据源")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise RuntimeError("BATMAN 官方下载地址不能包含凭据或非标准端口")
    return url


def _get(session, url, **kwargs):
    url = _official_url(url)
    for attempt in range(4):
        response = session.get(url, timeout=(5, 15), allow_redirects=False, **kwargs)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise RuntimeError("BATMAN 官网重定向缺少地址")
            url = _official_url(urljoin(url, location))
            continue
        response.raise_for_status()
        return response, url
    raise RuntimeError("BATMAN 官网重定向次数过多")


def _find_downloads(session, configured):
    if configured is not None:
        if not isinstance(configured, dict) or set(configured) != set(local.REQUIRED_FILES):
            raise RuntimeError("download_urls 必须明确提供 herbs、known_by_ingredients、known_by_targets 三个官方地址")
        return {kind: _official_url(url) for kind, url in configured.items()}
    pending, visited, found = [OFFICIAL_URL], set(), {}
    while pending and len(visited) < 4:
        page = pending.pop(0)
        if page in visited:
            continue
        visited.add(page)
        response, final_url = _get(session, page)
        try:
            if len(response.content) > 2 * 1024 * 1024:
                raise RuntimeError("BATMAN 下载页内容过大，拒绝将文件当作网页解析")
            parser = DownloadLinks()
            parser.feed(response.content.decode("utf-8", errors="replace"))
        finally:
            response.close()
        for href, label in parser.links:
            if not href:
                continue
            link = urljoin(final_url, href)
            try:
                _official_url(link)
            except RuntimeError:
                continue
            filename = unquote(Path(urlparse(link).path).name)
            for kind, required in local.REQUIRED_FILES.items():
                if filename == required or label.strip() == required:
                    found[kind] = link
            if any(word in (href + " " + label).lower() for word in ("download", "下载")):
                if not filename.endswith((".gz", ".zip", ".txt")) and link not in visited:
                    pending.append(link)
        if set(found) == set(local.REQUIRED_FILES):
            return found
    raise RuntimeError("官网未提供可识别的 BATMAN v2.0 known 下载链接；不猜测接口。请配置经过核实的官方 download_urls 或同步本地全量文件")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_files(directory):
    for kind, filename in local.REQUIRED_FILES.items():
        path = directory / filename
        with local._open_text(path) as stream:
            columns = set(stream.readline().strip().split("\t"))
        if not HEADERS[kind].issubset(columns):
            raise RuntimeError("BATMAN 官方文件结构不符：" + filename + "；拒绝使用验证码页、错误页或不兼容数据")


def _cached(directory):
    manifest = read_json(directory / "provenance.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise RuntimeError("BATMAN 缓存来源记录格式错误")
    for kind, filename in local.REQUIRED_FILES.items():
        record = manifest["files"][kind]
        if not isinstance(record, dict):
            raise RuntimeError("BATMAN 缓存文件来源记录格式错误")
        _official_url(record["url"])
        if _sha256(directory / filename) != record["sha256"]:
            raise RuntimeError("BATMAN 下载缓存哈希不符，请检查缓存，不自动覆盖")
    _validate_files(directory)
    return manifest


def acquire_official_cache(directory=None):
    directory = Path(directory) if directory is not None else CACHE_DIR
    with _CACHE_LOCK:
        if directory.exists():
            return directory, _cached(directory), False
        config_path = ROOT / "configs/batman_online.local.json"
        config = read_json(config_path) if config_path.is_file() else {}
        if not isinstance(config, dict):
            raise RuntimeError("BATMAN 在线配置必须是 JSON 对象")
        directory.parent.mkdir(parents=True, exist_ok=True)
        with requests.Session() as session, tempfile.TemporaryDirectory(dir=directory.parent) as temporary:
            staging = Path(temporary) / "data"
            staging.mkdir()
            links = _find_downloads(session, config.get("download_urls"))
            records = {}
            for kind, filename in local.REQUIRED_FILES.items():
                response, final_url = _get(session, links[kind], stream=True)
                path = staging / filename
                size = 0
                try:
                    with path.open("wb") as stream:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            size += len(chunk)
                            if size > MAX_DOWNLOAD_BYTES:
                                raise RuntimeError("BATMAN 文件超出下载大小限制")
                            stream.write(chunk)
                finally:
                    response.close()
                records[kind] = {"url": final_url, "sha256": _sha256(path), "bytes": size}
            _validate_files(staging)
            manifest = {"source": "BATMAN-TCM official full download", "accessed_at": now(), "files": records}
            write_json(staging / "provenance.json", manifest)
            staging.rename(directory)
        return directory, manifest, True


def query_targets(herb_candidates, threshold=0.84, *, local_query=None):
    query = local_query or local.query_local_targets
    try:
        result = query(herb_candidates, threshold=threshold)
    except local.MissingLocalData as error:
        if os.environ.get("PHARM_BATMAN_ONLINE_FALLBACK", "true").lower() in {"0", "false", "no"}:
            raise RuntimeError(str(error) + "；BATMAN 在线回退已关闭") from error
        try:
            directory, manifest, downloaded = acquire_official_cache()
            result = local.query_local_targets(herb_candidates, threshold=threshold, task={"batman_local_dir": str(directory)})
        except (requests.RequestException, OSError, ValueError, RuntimeError, KeyError) as online_error:
            raise RuntimeError(str(error) + "；官方在线回退未完成：" + str(online_error)
                               + "。请同步 BATMAN v2.0 本地文件或检查官网访问与下载链接；不会用合成数据替代。") from online_error
        result["retrieval"] = {"mode": "online_download" if downloaded else "cached_official_download", "local_missing": str(error), "provenance": manifest}
        return result
    result["retrieval"] = {"mode": "local"}
    return result
