"""Synthetic collection orchestration only; no browser or external model calls."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from pharm.chat import config, engine, tools, cnki_bridge
from pharm.core.common import write_json, now
from pharm.literature import dataset


def publish(root):
    source = root / "fixture/cnki_示例方"
    source.mkdir(parents=True, exist_ok=True)
    paper = {"rank": 1, "title": "示例方与示例病的工程记录", "abstract": "示例方与示例病相关，仅为工程样本。", "authors": "示例作者", "source": "工程样本", "date": "2026-11-02", "is_modified": False, "url": "https://kns.cnki.net/fixture/1"}
    write_json(source / "papers.json", {"keyword": "示例方", "papers": [paper]})
    triple = {"head": "示例方", "head_type": "方剂", "relation": "主治", "tail": "示例病", "tail_type": "疾病", "sources": [{"rank": 1, "title": paper["title"], "url": paper["url"], "evidence": paper["abstract"], "is_modified": False}]}
    (source / "triples.jsonl").write_text(json.dumps(triple, ensure_ascii=False), encoding="utf-8")
    dataset.import_data(source.parent, root / "data/cnki", keywords=["示例方"], update=True)


class FakeCollections:
    def __init__(self, pause=False): self.starts=0; self.resumes=0; self.cancels=0; self.polls=0; self.pause=pause; self.status_value="running"
    def start(self, root, keyword, request_id, **kwargs):
        self.starts+=1; self.selected=kwargs.get("model_selection"); self.id="cnki_collect_"+"a"*32
        return self.status(root,self.id)
    def status(self, root, job_id):
        self.polls+=1
        if self.status_value=="running" and self.polls>=3:
            if self.pause: self.status_value="failed_or_partial"
            else: publish(Path(root)); self.status_value="succeeded"
        return {"id":job_id,"keyword":"示例方","status":self.status_value,"phase":"collecting","paper_count":1,"abstract_count":1,"pages_recorded":1,"extracted_papers":1,"started_at":now(),"error":"请处理登录验证" if self.status_value=="failed_or_partial" else None}
    def resume(self, root, job_id, **kwargs):
        self.resumes+=1; self.pause=False; self.status_value="running"
        return self.status(root,job_id)
    def cancel(self, root, job_id):
        self.cancels+=1; self.status_value="cancelled"
        return self.status(root,job_id)


def setup(root, monkeypatch, pause=False):
    monkeypatch.delenv("PHARM_CNKI_DATA_DIR", raising=False)
    config.save(root,{"model":"fixture","base_url":"http://127.0.0.1:9/v1","storage":"session"})
    fake=FakeCollections(pause)
    monkeypatch.setattr(cnki_bridge.jobs,"manager",fake)
    monkeypatch.setattr(cnki_bridge,"POLL_SECONDS",.01)
    return fake


async def model(settings,messages,definitions):
    assert not definitions or all(d["function"]["name"] not in {"search_online_literature","query_pharmacology","read_research"} for d in definitions)
    has_evidence=any('[{"id": "E' in (m.get("content") or "") or '"evidence_id": "E' in (m.get("content") or "") for m in messages)
    if has_evidence: return {"content":"工程记录中的候选为示例病。[E1]","tool_calls":[]}
    return {"content":"","tool_calls":[{"id":"collect1","type":"function","function":{"name":"collect_cnki_literature","arguments":'{"keyword":"示例方","kind":"formula"}'}}]}


def test_collection_waits_then_answers_and_does_not_repeat_on_status(tmp_path,monkeypatch):
    fake=setup(tmp_path,monkeypatch)
    async def scenario():
        manager=engine.ResearchManager(complete=model)
        body={"session_id":uuid4().hex,"request_id":uuid4().hex,"text":"只进行CNKI的文献检索，示例方有哪些候选疾病？","network":True}
        manager.start(tmp_path,body)
        task=manager.tasks[(str(tmp_path.resolve()),body["request_id"])]
        await task
        result=manager.status(tmp_path,body["request_id"])
        assert result["status"]=="succeeded",result.get("error")
        assert result["source_scope"]=="cnki" and fake.starts==1
        assert result["cnki_result"]["items"][0]["names"]==["示例病"]
        assert result["cited_ids"]==["E1"] and result["finished_at"]
        manager.status(tmp_path,body["request_id"])
        assert fake.starts==1 and not manager.working
    asyncio.run(scenario())


def test_attention_resume_reuses_collection_and_finishes(tmp_path,monkeypatch):
    fake=setup(tmp_path,monkeypatch,pause=True)
    async def scenario():
        manager=engine.ResearchManager(complete=model)
        body={"session_id":uuid4().hex,"request_id":uuid4().hex,"text":"只查知网，示例方的候选疾病","network":True}
        manager.start(tmp_path,body)
        await manager.tasks[(str(tmp_path.resolve()),body["request_id"])]
        assert manager.status(tmp_path,body["request_id"])["status"]=="needs_attention"
        await manager.resume_cnki(tmp_path,body["request_id"])
        await manager.tasks[(str(tmp_path.resolve()),body["request_id"])]
        assert manager.status(tmp_path,body["request_id"])["status"]=="succeeded"
        assert fake.starts==1 and fake.resumes==1
    asyncio.run(scenario())


def test_scope_guards_other_sources_and_no_network_never_starts_collection(tmp_path,monkeypatch):
    fake=setup(tmp_path,monkeypatch)
    assert cnki_bridge.resolve_scope("只进行CNKI的检索", "all")=="cnki"
    assert cnki_bridge.resolve_scope("不要只查CNKI", "auto")=="all"
    state={"source_scope":"cnki","evidence":[{"id":"E1","source":"Europe PMC"}]}
    async def scenario():
        blocked=await tools.execute("search_online_literature",{"query":"test"},state,tmp_path,True)
        assert blocked["status"]=="disabled"
        assert (await tools.execute("read_evidence",{"evidence_id":"E1"},state,tmp_path,True))["status"]=="not_found"
    asyncio.run(scenario())
    (tmp_path / "data/cnki").mkdir(parents=True, exist_ok=True)
    result=cnki_bridge.start(tmp_path,{"id":uuid4().hex},{"keyword":"示例方","kind":"formula"},False,{})
    assert result["status"]=="disabled" and fake.starts==0
