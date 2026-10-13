import gzip

import pytest
import requests

from pharm.batman import local, resolver


def test_local_success_and_empty_result_never_go_online(monkeypatch):
    monkeypatch.setattr(resolver, "acquire_official_cache", lambda: pytest.fail("local lookup contacted website"))
    for genes in (["TP53"], []):
        result = resolver.query_targets({"白芍": ["白芍"]}, local_query=lambda *args, **kwargs: {"genes": genes, "unmatched": []})
        assert result["genes"] == genes
        assert result["retrieval"]["mode"] == "local"


@pytest.mark.parametrize("error", [ValueError("invalid threshold"), OSError("corrupt file")])
def test_bad_local_data_does_not_silently_go_online(monkeypatch, error):
    monkeypatch.setattr(resolver, "acquire_official_cache", lambda: pytest.fail("corrupt data triggered download"))

    def broken(*args, **kwargs):
        raise error

    with pytest.raises(type(error), match=str(error)):
        resolver.query_targets({"白芍": ["白芍"]}, local_query=broken)


def missing(*args, **kwargs):
    raise local.MissingLocalData("本地 BATMAN 文件缺失")


@pytest.mark.parametrize("downloaded, mode", [(True, "online_download"), (False, "cached_official_download")])
def test_missing_local_uses_official_cache_with_traceability(monkeypatch, tmp_path, downloaded, mode):
    monkeypatch.setattr(resolver, "acquire_official_cache", lambda: (tmp_path, {"source": "official"}, downloaded))

    def cached_query(herbs, threshold, task):
        assert herbs == {"白芍": ["白芍"]}
        assert task == {"batman_local_dir": str(tmp_path)}
        assert threshold == 0.84
        return {"genes": ["TP53"]}

    monkeypatch.setattr(local, "query_local_targets", cached_query)
    result = resolver.query_targets({"白芍": ["白芍"]}, local_query=missing)
    assert result["genes"] == ["TP53"]
    assert result["retrieval"]["mode"] == mode
    assert result["retrieval"]["provenance"]["source"] == "official"


def test_disabled_fallback_never_contacts_network(monkeypatch):
    monkeypatch.setenv("PHARM_BATMAN_ONLINE_FALLBACK", "false")
    monkeypatch.setattr(resolver, "acquire_official_cache", lambda: pytest.fail("fallback disabled"))
    with pytest.raises(RuntimeError, match="回退已关闭"):
        resolver.query_targets({"白芍": ["白芍"]}, local_query=missing)


def test_online_failure_is_not_a_synthetic_success(monkeypatch):
    def unavailable():
        raise requests.Timeout("site timeout")

    monkeypatch.setattr(resolver, "acquire_official_cache", unavailable)
    with pytest.raises(RuntimeError, match="官方在线回退未完成.*site timeout"):
        resolver.query_targets({"白芍": ["白芍"]}, local_query=missing)


@pytest.mark.parametrize("url", ["https://example.com/file.gz", "file:///tmp/test", "http://bionet.ncpsb.org.cn:1234/file", "http://user:pass@bionet.ncpsb.org.cn/file", None])
def test_non_official_downloads_are_rejected(url):
    with pytest.raises(RuntimeError):
        resolver._official_url(url)


def test_download_cache_is_complete_validated_and_reused(monkeypatch, tmp_path):
    monkeypatch.setattr(resolver, "ROOT", tmp_path)
    payloads = {
        "herb_browse.txt": b"Chinese.Name\tIngredients\n",
        "known_browse_by_ingredients.txt.gz": gzip.compress(b"PubChem_CID\tknown_target_proteins\n"),
        "known_browse_by_targets.txt.gz": gzip.compress(b"entrez_gene_id\tentrez_gene_symbol\n"),
    }
    page = "".join('<a href="download/' + filename + '">' + filename + "</a>" for filename in payloads).encode()
    calls = []

    class Response:
        status_code = 200
        headers = {}

        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield self.content

        def close(self):
            pass

    def get(session, url, **kwargs):
        calls.append(url)
        return Response(payloads.get(url.rsplit("/", 1)[-1], page))

    monkeypatch.setattr(requests.Session, "get", get)
    cache = tmp_path / "cache"
    directory, manifest, downloaded = resolver.acquire_official_cache(cache)
    assert directory == cache and downloaded is True
    assert len(calls) == 4
    assert set(manifest["files"]) == set(local.REQUIRED_FILES)
    assert all(len(record["sha256"]) == 64 for record in manifest["files"].values())
    assert resolver.acquire_official_cache(cache)[2] is False
    assert len(calls) == 4
    (cache / "herb_browse.txt").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="哈希不符"):
        resolver.acquire_official_cache(cache)
    assert len(calls) == 4


def test_error_html_never_passes_as_official_data(tmp_path):
    for filename in local.REQUIRED_FILES.values():
        content = b"<html>please verify access</html>"
        (tmp_path / filename).write_bytes(gzip.compress(content) if filename.endswith(".gz") else content)
    with pytest.raises(RuntimeError, match="文件结构不符"):
        resolver._validate_files(tmp_path)


def test_official_redirect_to_untrusted_host_is_blocked(monkeypatch):
    class Response:
        status_code = 302
        headers = {"Location": "https://untrusted.example/data"}

        def close(self):
            pass

    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: Response())
    with requests.Session() as session, pytest.raises(RuntimeError, match="官方站点"):
        resolver._get(session, resolver.OFFICIAL_URL)
