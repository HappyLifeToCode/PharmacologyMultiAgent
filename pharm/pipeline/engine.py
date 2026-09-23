"""四阶段反向发现流水线：preflight → herb_targets → disease_reverse → review。

纯程序确定性执行，无模型会话；本地数据缺失时阶段置 blocked 并给出升级点
指引，不合成顶替（fixture 模式除外，其产物一律标注 synthetic_engineering）。
manifest.json 是唯一状态源；attempt_NN 不可变；resume 只复用签名与产物
哈希一致的成功阶段。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path

from ..core.common import (ROOT, digest, now, read_json, safe_name, task_list,
                            write_json, public_artifact, workspace_identity, owned_run)
from ..core.archive import archive_run
from ..batman import local as batman_local
from ..batman.formulas import resolve_batman_names
from ..discovery import query as discovery
from ..network import cytoscape, metrics as network_metrics, string_local
from ..enrich import david
from ..agents import runtime
from . import scheduler
from .scheduler import execute_graph

WORKFLOW_VERSION = 4
PIPELINE_LABELS = {
    "discovery": {"preflight": "本地数据预检", "herb_targets": "药材靶点解析",
                  "disease_reverse": "疾病反向查询", "review": "程序验收与报告"},
    "analysis": {"shared_targets": "共同靶点提取", "network": "网络分析",
                 "enrichment": "富集分析", "analysis_review": "验收与报告"},
}
# 兼容引用：discovery 流水线标签
LABELS = PIPELINE_LABELS["discovery"]
LOCK = threading.RLock()
ACTIVE = set()

BATMAN_GUIDANCE = ("BATMAN 本地数据不可用：请将 v2.0 全量下载放入数据目录，或在 "
                   "configs/batman_data.local.json 配置 data_dir；在线采集升级点尚未实现（后续阶段）")
INDEX_GUIDANCE = ("本地疾病索引不可用：请用合格导入批次执行 python -m pharm.discovery.query prepare "
                  "建立索引；在线采集升级点尚未实现（后续阶段）")
BATMAN_ASSIST = ("BATMAN 本地数据未配置。可在工作台启动在线采集协助会话，"
                 "由您在内嵌浏览器中完成人机验证后继续。")
INDEX_ASSIST = ("本地疾病索引未准备。可在工作台启动在线采集协助会话，"
                "由您在内嵌浏览器中完成人机验证后继续。")
SYNTHETIC = "synthetic_engineering"

# live + agents=true 时，程序计算完成后由对应 Agent 会话核验（结论不改变程序产物）。
# 角色提示词在 agents/<角色>.md，内容哈希参与阶段 input_signature。
AGENT_ROLES = {"herb_targets": "batman_targets", "disease_reverse": "disease_discovery", "review": "review"}
ANALYSIS_AGENT_ROLES = {"network": "network_analysis", "enrichment": "enrichment_analysis",
                        "analysis_review": "review"}
AGENT_INSTRUCTIONS = {
    "herb_targets": "核验 herb_targets 阶段的药材靶点产物（计数一致性、未命中药材、known/predicted 分布、provenance 完整性），按角色文件清单逐项核对。",
    "disease_reverse": "核验 disease_reverse 阶段的疾病反查产物（候选计数、置信度组件方向、零匹配如实性），并在 findings 中用中文写一段面向研究者的结果解释。",
    "review": "程序验收已通过为前提，核对全链证据完整性并写验收结论与遗留事项。",
    "network": "核验 network 阶段的网络产物：映射/未映射/歧义记录、孤立节点、边表分值范围、度值方法来源（NetworkX 或 CytoNCA 桥）是否如实标注。",
    "enrichment": "核验 enrichment 阶段的富集产物：识别率、背景选择依据、注释类别齐备、显著条目可回查；EASE 不得改标为普通超几何检验，零显著如实。",
    "analysis_review": "程序验收已通过为前提，核对机制分析链路证据完整性（共同靶点、网络、富集）并写验收结论与遗留事项。",
}


def _bounded(value, max_items=20, _depth=0):
    """Agent 证据裁剪：长列表变为计数+样例，大结果集不整体进 prompt。"""
    if isinstance(value, dict):
        return {k: _bounded(v, max_items, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) <= max_items or _depth == 0:
            return [_bounded(v, max_items, _depth + 1) for v in value]
        return {"count": len(value), "sample": [_bounded(v, max_items, _depth + 1)
                                                for v in value[:5]]}
    return value

FIXTURE_GENES = ["TP53", "EGFR", "AKT1", "TNF", "IL6", "VEGFA"]
FIXTURE_ASSOCIATIONS = [
    ("TP53", "Hyperthyroidism", "genecards", 2, 10.0),
    ("VEGFA", "Hyperthyroidism", "omim", 2, None),
    ("EGFR", "Hypothyroidism", "genecards", 2, 8.0),
    ("AKT1", "Thyroid cancer", "genecards", 2, 7.5),
    ("TNF", "Thyroid nodules", "omim", 2, None),
    ("IL6", "Thyroiditis", "genecards", 2, 9.0),
]


def _fixture_targets(herbs):
    """固定合成药材靶点表，仅用于 fixture 工程验证。"""
    relations = []
    for index, herb in enumerate(herbs):
        relations.append({"herb": herb, "batman_name": herb, "compound_id": "SYN%02dA" % index,
                          "compound_name": "synthetic", "gene_symbol": FIXTURE_GENES[index % len(FIXTURE_GENES)],
                          "score": None, "evidence": "known"})
        relations.append({"herb": herb, "batman_name": herb, "compound_id": "SYN%02dB" % index,
                          "compound_name": "synthetic", "gene_symbol": FIXTURE_GENES[(index + 2) % len(FIXTURE_GENES)],
                          "score": 0.9, "evidence": "predicted"})
    genes = sorted({row["gene_symbol"] for row in relations})
    return {"evidence_type": SYNTHETIC, "herbs": list(herbs), "threshold": 0.84,
            "relations": relations, "genes": genes, "unmatched_herbs": [], "rejected_symbols": [],
            "per_herb": {herb: {"batman_name": herb, "compounds": 2, "relations": 2} for herb in herbs},
            "source_counts": {"relations": len(relations), "unique_genes": len(genes),
                              "known_rows": len(herbs), "predicted_rows": len(herbs), "unmatched_herbs": 0},
            "provenance": {"synthetic": True, "note": "固定合成集合，仅供工程验证，非药理数据"}}


def _write_fixture_index(path):
    """小型合成疾病索引（复用 discovery 的 sqlite schema），仅供 fixture 工程验证。"""
    metadata = {
        "schema_version": 1, "created_at": now(), "batch_name": SYNTHETIC,
        "diseases": list(discovery.DISEASES), "herbs": [],
        "source_rows": {"genecards": 4, "omim": 2},
        "selection": SYNTHETIC, "identifier_policy": "exact_symbol_no_alias_mapping",
        "disease_identifier_policy": "source_query_labels_not_ontology_ids",
        "provenance": {"synthetic": True, "note": "合成工程验证索引，非真实来源"},
        "source_sha256": {}, "limitation": "合成工程验证索引；不代表任何真实疾病关联。",
        "evidence_type": SYNTHETIC,
    }
    path = Path(path)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.executescript("""
                CREATE TABLE metadata (value TEXT NOT NULL);
                CREATE TABLE associations (
                    gene TEXT NOT NULL, disease TEXT NOT NULL, source TEXT NOT NULL,
                    source_row INTEGER NOT NULL, score REAL, record TEXT NOT NULL);
                CREATE INDEX association_gene ON associations(gene);
                CREATE INDEX association_disease ON associations(disease, gene);
                CREATE TABLE herb_relations (
                    herb TEXT NOT NULL, compound TEXT NOT NULL, gene TEXT NOT NULL,
                    evidence TEXT NOT NULL, score REAL);
                CREATE INDEX herb_name ON herb_relations(herb);
            """)
            connection.execute("INSERT INTO metadata VALUES (?)", (json.dumps(metadata, ensure_ascii=False),))
            connection.executemany("INSERT INTO associations VALUES (?,?,?,?,?,?)", [
                (gene, disease, source, row, score, json.dumps({"synthetic": True, "gene_symbol": gene, "disease": disease}, ensure_ascii=False))
                for gene, disease, source, row, score in FIXTURE_ASSOCIATIONS
            ])
    return metadata


def _availability(task):
    """检查 BATMAN 本地数据与疾病索引可用性；不可用项附升级点 guidance。"""
    result = {}
    try:
        root = batman_local._data_root(task)
        missing = [name for name in batman_local.REQUIRED_FILES.values() if not (root / name).is_file()]
        predicted = all(any((root / name).is_file() for name in names)
                        for names in batman_local.PREDICTED_FILES.values())
        entry = {"available": not missing, "root": str(root), "missing_files": missing,
                 "predicted_files": predicted}
        if missing:
            entry["guidance"] = BATMAN_GUIDANCE
        elif not predicted:
            entry["note"] = "predicted 文件缺失，将只使用 known 证据"
        result["batman"] = entry
    except ValueError as exc:
        result["batman"] = {"available": False, "error": str(exc), "guidance": BATMAN_GUIDANCE}
    try:
        database = discovery.database_path(ROOT)
        if not database.is_file():
            result["discovery_index"] = {"available": False, "database": str(database), "guidance": INDEX_GUIDANCE}
        else:
            with closing(discovery._connect(database)) as connection:
                metadata = discovery._metadata(connection)
            result["discovery_index"] = {"available": True, "database": str(database),
                                         "disease_count": len(metadata["diseases"]),
                                         "diseases": metadata["diseases"][:10],
                                         "diseases_truncated": len(metadata["diseases"]) > 10,
                                         "created_at": metadata["created_at"]}
    except (ValueError, OSError, sqlite3.Error) as exc:
        result["discovery_index"] = {"available": False, "error": str(exc), "guidance": INDEX_GUIDANCE}
    return result


def _reverse_lookup(database, genes, chunk_size=3000):
    """调 discovery.query 反查；超过单次上限时分块查询并合并（如实记录分块）。"""
    genes = list(genes)
    if len(genes) <= chunk_size:
        return discovery.query(database, genes=genes), {"chunked": False}
    chunks = [genes[i:i + chunk_size] for i in range(0, len(genes), chunk_size)]
    parts = [discovery.query(database, genes=part) for part in chunks]
    diseases = [candidate["disease"] for candidate in parts[0]["candidates"]]
    candidates = []
    for position, disease in enumerate(diseases):
        rows = [row for part in parts for row in part["candidates"][position]["evidence"]]
        matched = sorted({row["gene_symbol"] for row in rows})
        indexed = parts[0]["candidates"][position]["indexed_target_count"]
        per_source = {}
        for row in rows:
            per_source.setdefault(row["source"], set()).add(row["gene_symbol"])
        candidates.append({
            "disease": disease, "matched_genes": matched, "matched_count": len(matched),
            "indexed_target_count": indexed,
            "input_coverage": len(matched) / len(genes),
            "disease_coverage": len(matched) / indexed if indexed else None,
            "source_gene_counts": {source: len(symbols) for source, symbols in per_source.items()},
            "evidence": rows,
        })
    all_matched = {gene for candidate in candidates for gene in candidate["matched_genes"]}
    discovery.apply_confidence(database, candidates, len(genes))
    merged = dict(parts[0])
    merged.update({
        "input": {"herbs": None, "genes": genes}, "input_count": len(genes),
        "matched_input_count": len(all_matched),
        "unmatched_genes": sorted(set(genes) - all_matched),
        "candidates": candidates, "herb_relations": [],
        "stages": [
            {"id": "input_targets", "status": "succeeded", "count": len(genes)},
            {"id": "local_reverse_lookup", "status": "succeeded",
             "count": sum(len(candidate["evidence"]) for candidate in candidates)},
            {"id": "disease_evidence", "status": "succeeded",
             "count": sum(bool(candidate["matched_count"]) for candidate in candidates)},
        ],
    })
    return merged, {"chunked": True, "chunk_size": chunk_size, "chunks": len(chunks)}


class Runner:
    def __init__(self, run_id):
        self.run_id = safe_name(run_id)
        self.directory = ROOT / "runs" / run_id
        self.manifest_path = self.directory / "manifest.json"
        self.manifest = read_json(self.manifest_path)
        if self.manifest.get("workflow_version") != WORKFLOW_VERSION:
            raise ValueError("旧结构运行不能由新流水线恢复，请新建运行")
        self.task = self.manifest["task"]
        self.home = None
        self._agents = self.manifest["mode"] == "live" and self.task.get("agents") is True
        self.pipeline = self.manifest.get("pipeline", "discovery")
        self.stages = scheduler.stages_for(self.pipeline)
        self.labels = PIPELINE_LABELS[self.pipeline]
        self.agent_roles = ANALYSIS_AGENT_ROLES if self.pipeline == "analysis" else AGENT_ROLES

    def save(self):
        with LOCK:
            self.manifest["updated_at"] = now()
            write_json(self.manifest_path, self.manifest)

    def event(self, role, kind, message):
        with LOCK:
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"timestamp": now(), "role": role, "type": kind, "message": message}, ensure_ascii=False) + "\n")

    def begin(self, role):
        with LOCK:
            old = self.manifest["stages"].get(role, {})
            attempt = int(old.get("attempt", 0)) + 1
            directory = self.directory / role / ("attempt_%02d" % attempt)
            directory.mkdir(parents=True, exist_ok=False)
            self.manifest["stages"][role] = {"status": "running", "label": self.labels[role], "summary": "正在执行", "blockers": [], "artifacts": [], "attempt": attempt, "input_signature": self.signature(role), "started_at": now(), "directory": str(directory.relative_to(self.directory)).replace("\\", "/")}
            self.save()
        self.event(role, "stage.started", "开始第 %d 次尝试" % attempt)
        return directory

    def signature(self, role):
        inputs = {"task": self.task, "mode": self.manifest["mode"], "role": role,
                  "runtime": read_json(ROOT / "configs/runtime.json"),
                  "code": {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted((ROOT / "pharm").rglob("*.py"))}}
        if self.manifest["mode"] == "fixture":
            inputs["fixture"] = SYNTHETIC
        elif self.pipeline == "analysis":
            if role == "shared_targets":
                inputs["source"] = self._source_signature()
            if role == "network":
                try:
                    inputs["string_local_files"] = string_local.string_local_signature(self.task)
                except (ValueError, OSError) as exc:
                    inputs["string_local_files"] = {"unavailable": str(exc)}
        else:
            if role in ("preflight", "herb_targets"):
                inputs["batman_data"] = self._batman_signature()
            if role in ("disease_reverse", "review"):
                try:
                    database = discovery.database_path(ROOT)
                    inputs["discovery_db"] = digest(database) if database.is_file() else None
                except ValueError as exc:
                    inputs["discovery_db"] = {"unavailable": str(exc)}
        upstream = {"discovery": {"disease_reverse": ["herb_targets"], "review": ["herb_targets", "disease_reverse"]},
                    "analysis": {"network": ["shared_targets"], "enrichment": ["shared_targets"],
                                 "analysis_review": ["network", "enrichment"]}}[self.pipeline].get(role, [])
        inputs["upstream"] = {key: digest(self.directory / path)
                              for key, path in self.manifest.get("verified_targets", {}).items()
                              if key in upstream and (self.directory / path).is_file()}
        if self._agents and role in self.agent_roles:
            prompt = ROOT / "agents" / (self.agent_roles[role] + ".md")
            inputs["agent_prompt"] = digest(prompt) if prompt.is_file() else None
        return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _source_signature(self):
        """analysis 流水线的来源运行与疾病索引快照（签名用）。"""
        try:
            info = self.manifest.get("analysis", {})
            source_dir = ROOT / "runs" / info["source_run_id"]
            out = {"manifest": digest(source_dir / "manifest.json")}
            rel = read_json(source_dir / "manifest.json").get("verified_targets", {}).get("disease_reverse")
            database = read_json(source_dir / rel).get("database") if rel else None
            out["discovery_db"] = digest(database) if database and Path(database).is_file() else None
            return out
        except (OSError, ValueError, KeyError) as exc:
            return {"unavailable": str(exc)}

    def _batman_signature(self):
        try:
            root = batman_local._data_root(self.task)
        except ValueError as exc:
            return {"unavailable": str(exc)}
        names = list(batman_local.REQUIRED_FILES.values())
        for alternatives in batman_local.PREDICTED_FILES.values():
            names.extend(alternatives)
        files = {}
        for name in names:
            path = root / name
            if path.is_file():
                files[name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
        return {"root": str(root), "files": files}

    def reuse(self, role):
        stage = self.manifest["stages"].get(role, {})
        if stage.get("status") != "succeeded" or stage.get("input_signature") != self.signature(role):
            return False
        hashes = stage.get("artifact_sha256", {})
        if not hashes or any(not (self.directory / name).is_file() or digest(self.directory / name) != value for name, value in hashes.items()):
            return False
        self.event(role, "stage.reused", "输入、参数与产物哈希一致，复用已验收结果")
        return True

    def finish(self, role, result, directory):
        # Only artifacts inside this run are eligible for display.
        artifacts = []
        for filename in result.get("artifacts", []):
            path = Path(filename)
            if not path.is_absolute():
                path = directory / path
            try:
                relative = path.resolve().relative_to(self.directory.resolve())
            except ValueError:
                continue
            if path.is_file() and public_artifact(relative):
                artifacts.append(relative.as_posix())
        handoff = dict(result, run_id=self.run_id, task_id=self.task["task_id"], agent_role=role, recorded_at=now(), artifacts=sorted(set(artifacts)))
        handoff["artifact_sha256"] = {p: digest(self.directory / p) for p in handoff["artifacts"]}
        write_json(directory / "handoff.json", handoff)
        artifacts.append((directory / "handoff.json").relative_to(self.directory).as_posix())
        with LOCK:
            unique = sorted(set(artifacts))
            self.manifest["stages"][role].update(status=result["status"], summary=result["summary"], blockers=result.get("blockers", []), findings=result.get("findings", []), artifacts=unique, artifact_sha256={p: digest(self.directory / p) for p in unique if (self.directory / p).is_file()}, finished_at=now())
            self.save()
        self.event(role, "stage.completed", result["summary"])
        if self.manifest.get("mode") == "live":
            with LOCK:
                try:
                    self.manifest["archive"] = archive_run(self.directory, ROOT / "data/pharm")
                    self.manifest.pop("archive_error", None)
                except (ValueError, OSError, KeyError) as exc:
                    self.manifest["archive_error"] = str(exc)
                    self.event("system", "archive.failed", str(exc))
                self.save()

    def _check_agents(self):
        """多 Agent 模式开工前检查：Codex CLI 存在 + 本机 profile 可用。"""
        if not (shutil.which("codex.exe") or shutil.which("codex")):
            return False, "找不到 Codex CLI"
        try:
            self.home = runtime.prepare_home()
        except Exception as exc:
            return False, str(exc)
        return True, None

    def _agent_session(self, agent_role, directory, instruction, evidence):
        definition = (ROOT / "agents" / (agent_role + ".md")).read_text(encoding="utf-8-sig")
        prompt = "\n".join([
            "你是药理反向发现流水线的独立核验会话。使用中文。范围只限本次核验：不修改任何文件，不读取账号文件，不安装软件，不访问网络，不重新计算。",
            "所有数字由确定性程序产出；你的职责是核验与解释，不得修改或“修正”程序产物中的数字；发现不一致如实上报。",
            "角色：" + agent_role,
            definition,
            "任务：" + json.dumps(self.task, ensure_ascii=False),
            "证据（大列表已按计数+样例裁剪）：" + json.dumps(_bounded(evidence), ensure_ascii=False),
            instruction,
            "返回结构化 JSON：status（succeeded/partial/blocked/failed）、summary、findings、confidence（high/medium/low 自评核验把握，理由写入 findings）；blockers、artifacts 填空数组；graph、rework 填 null。",
        ])
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        return runtime.execute(prompt, directory,
                               timeout=int(os.environ.get("PHARM_AGENT_TIMEOUT", "900")),
                               home=self.home)

    def _maybe_agent_review(self, role, result, directory, evidence):
        """程序计算完成且成功后由对应 Agent 核验；Agent 结论不改变程序产物。"""
        if not self._agents or result.get("status") != "succeeded" or role not in self.agent_roles:
            return result
        agent_role = self.agent_roles[role]
        self.event(role, "agent.started", "启动 " + agent_role + " 核验会话")
        try:
            agent_result, meta = self._agent_session(agent_role, directory / "agent",
                                                     AGENT_INSTRUCTIONS[role], evidence)
            review = {"agent_role": agent_role, "session_id": meta.get("session_id"),
                      "status": agent_result["status"], "summary": agent_result.get("summary"),
                      "confidence": agent_result.get("confidence"),
                      "findings": agent_result.get("findings", []),
                      "model": meta.get("model"), "elapsed_seconds": meta.get("elapsed_seconds")}
            result["agent_review"] = review
            result.setdefault("artifacts", []).append("agent/execution.json")
            self.event(role, "agent.completed", "%s：%s" % (agent_role, agent_result["status"]))
            if agent_result["status"] != "succeeded":
                result["status"] = "partial"
                result.setdefault("blockers", []).append("Agent 核验未完成或未通过：" + str(agent_result.get("summary", "")))
        except Exception as exc:
            result["agent_review"] = {"agent_role": agent_role, "error": str(exc)}
            result.setdefault("findings", []).append("Agent 核验未完成：" + str(exc))
            result["status"] = "partial"
            result.setdefault("blockers", []).append("Agent 核验未完成：" + str(exc))
            self.event(role, "agent.error", str(exc))
        return result

    def _coordinator_opening(self):
        """preflight 之后、herb_targets 之前的协调开场核对；失败不阻塞流水线。"""
        if not self._agents:
            return
        preflight = self.manifest["stages"].get("preflight", {})
        availability = None
        for path in preflight.get("artifacts", []):
            if path.endswith("availability.json") and (self.directory / path).is_file():
                availability = read_json(self.directory / path)
        try:
            result, meta = self._agent_session("coordinator", self.directory / "coordinator_opening",
                                               "核对任务参数与 preflight 可用性报告：参数是否合理、本地数据缺口是否属实、指引是否可执行。无问题则确认。",
                                               {"task": self.task, "preflight_status": preflight.get("status"),
                                                "availability": availability})
            self.manifest["coordinator_opening"] = {"status": result["status"], "summary": result.get("summary"),
                                                    "session_id": meta.get("session_id")}
            self.event("coordinator", "agent.completed", "开场核对：" + result["status"])
        except Exception as exc:
            self.manifest["coordinator_opening"] = {"error": str(exc)}
            self.event("coordinator", "agent.error", str(exc))
        self.save()

    def _herb_stage_with_opening(self):
        self._coordinator_opening()
        return self.herb_targets_stage()

    def preflight_stage(self):
        role = "preflight"
        if self.reuse(role):
            return
        directory = self.begin(role)
        try:
            if self.manifest["mode"] == "fixture":
                availability = {
                    "batman": {"available": True, "evidence_type": SYNTHETIC, "note": "合成工程验证，不访问真实 BATMAN 数据"},
                    "discovery_index": {"available": True, "evidence_type": SYNTHETIC, "note": "合成工程验证，使用内置合成索引"},
                }
            else:
                availability = _availability(self.task)
            write_json(directory / "availability.json", availability)
            unavailable = [name for name, item in availability.items() if not item.get("available")]
            self.finish(role, {
                "status": "succeeded",
                "summary": "本地数据全部可用" if not unavailable else "不可用：" + "、".join(unavailable),
                "blockers": [],
                "findings": [item["guidance"] for item in availability.values() if item.get("guidance")],
                "artifacts": ["availability.json"],
            }, directory)
        except Exception as exc:
            self.finish(role, {"status": "failed", "summary": str(exc), "blockers": [str(exc)], "artifacts": []}, directory)

    def herb_targets_stage(self):
        role = "herb_targets"
        if self.reuse(role):
            with LOCK:
                stage = self.manifest["stages"][role]
                path = next(p for p in stage["artifacts"] if p.endswith("/targets.json"))
                self.manifest.setdefault("verified_targets", {})[role] = path
                output = read_json(self.directory / path)
                self.manifest["metrics"].update(unique_targets=len(output["genes"]),
                                                herb_relations=len(output["relations"]),
                                                unmatched_herbs=len(output.get("unmatched_herbs", [])))
                self.save()
            return
        directory = self.begin(role)
        try:
            if self.manifest["mode"] == "fixture":
                output = _fixture_targets(self.task["herbs"])
            else:
                batman = _availability(self.task)["batman"]
                if not batman["available"]:
                    self.event(role, "assist_requested", BATMAN_ASSIST)
                    self.finish(role, {"status": "blocked",
                                       "summary": "BATMAN 本地数据不可用，未生成靶点（不以合成数据顶替）",
                                       "blockers": [batman.get("guidance", BATMAN_GUIDANCE)],
                                       "assist": {"available": True, "guidance": BATMAN_ASSIST},
                                       "artifacts": []}, directory)
                    return
                candidates = {herb: resolve_batman_names(herb) for herb in self.task["herbs"]}
                result = batman_local.query_local_targets(candidates, self.task.get("batman_threshold", 0.84), self.task)
                output = {
                    "evidence_type": "user_local_batman",
                    "herbs": list(self.task["herbs"]),
                    "threshold": result["threshold"],
                    "relations": result["relations"],
                    "genes": result["genes"],
                    "unmatched_herbs": result["unmatched"],
                    "rejected_symbols": result["rejected_symbols"],
                    "per_herb": result["per_herb"],
                    "source_counts": {"relations": len(result["relations"]), "unique_genes": len(result["genes"]),
                                      "known_rows": sum(row["evidence"] == "known" for row in result["relations"]),
                                      "predicted_rows": sum(row["evidence"] == "predicted" for row in result["relations"]),
                                      "unmatched_herbs": len(result["unmatched"])},
                    "provenance": {"data_version": result["data_version"], "source_url": result["source_url"],
                                   "accessed_at": self.task.get("batman_accessed_at"),
                                   "include_predicted": result["include_predicted"],
                                   "query": "local full-download files; no online access",
                                   "files": result["files"]},
                }
            write_json(directory / "targets.json", output)
            with LOCK:
                self.manifest.setdefault("verified_targets", {})[role] = str((directory / "targets.json").relative_to(self.directory)).replace("\\", "/")
                self.manifest["metrics"].update(unique_targets=len(output["genes"]),
                                                herb_relations=len(output["relations"]),
                                                unmatched_herbs=len(output["unmatched_herbs"]))
                self.save()
            findings = []
            if output["unmatched_herbs"]:
                findings.append("BATMAN 未收录或未命中药材：" + "、".join(output["unmatched_herbs"]))
            if self.manifest["mode"] == "fixture":
                findings.append("合成工程验证数据，非真实 BATMAN 查询结果")
            self.event(role, "tool.succeeded", "本地解析 %d 味药材，唯一靶点 %d 个" % (len(output["herbs"]), len(output["genes"])))
            result = {
                "status": "succeeded",
                "summary": "%d 味药材解析出 %d 个唯一靶点（关系 %d 行）%s" % (
                    len(output["herbs"]), len(output["genes"]), len(output["relations"]),
                    "（合成验证）" if self.manifest["mode"] == "fixture" else ""),
                "blockers": [], "findings": findings, "artifacts": ["targets.json"],
            }
            result = self._maybe_agent_review(role, result, directory, {
                "source_counts": output["source_counts"], "threshold": output["threshold"],
                "per_herb": output["per_herb"], "unmatched_herbs": output["unmatched_herbs"],
                "genes_total": len(output["genes"]), "genes_sample": output["genes"][:20],
                "provenance": output["provenance"]})
            self.finish(role, result, directory)
        except Exception as exc:
            self.finish(role, {"status": "failed", "summary": str(exc), "blockers": [str(exc)],
                               "artifacts": ["targets.json"] if (directory / "targets.json").is_file() else []}, directory)

    def disease_reverse_stage(self):
        role = "disease_reverse"
        if self.reuse(role):
            with LOCK:
                stage = self.manifest["stages"][role]
                path = next(p for p in stage["artifacts"] if p.endswith("/result.json"))
                self.manifest.setdefault("verified_targets", {})[role] = path
                result = read_json(self.directory / path)
                self._reverse_metrics(result)
                self.save()
            return
        directory = self.begin(role)
        try:
            herb_stage = self.manifest["stages"].get("herb_targets", {})
            if herb_stage.get("status") != "succeeded":
                self.finish(role, {"status": "blocked",
                                   "summary": "等待药材靶点解析成功后再反查",
                                   "blockers": ["herb_targets 未成功（当前 %s），未执行反查" % herb_stage.get("status", "pending")],
                                   "artifacts": []}, directory)
                return
            targets = read_json(self.directory / self.manifest["verified_targets"]["herb_targets"])
            genes = targets["genes"]
            chunked = {"chunked": False}
            if not genes:
                result = {"workflow": "five_disease_reverse_lookup_v1", "status": "succeeded",
                          "scientific_complete": False, "created_at": now(),
                          "limitation": "上游无可用靶点，未执行反查。" + discovery.LIMITATION,
                          "evidence_type": targets["evidence_type"],
                          "input": {"herbs": None, "genes": []}, "input_count": 0,
                          "matched_input_count": 0, "unmatched_genes": [], "herb_relations": [],
                          "candidates": [], "dataset": None, "database_sha256": None,
                          "stages": [{"id": "input_targets", "status": "succeeded", "count": 0},
                                     {"id": "local_reverse_lookup", "status": "skipped", "count": 0},
                                     {"id": "disease_evidence", "status": "skipped", "count": 0}]}
            else:
                if self.manifest["mode"] == "fixture":
                    database = directory / "synthetic_index.sqlite"
                    _write_fixture_index(database)
                else:
                    index = _availability(self.task)["discovery_index"]
                    if not index["available"]:
                        self.event(role, "assist_requested", INDEX_ASSIST)
                        self.finish(role, {"status": "blocked",
                                           "summary": "本地疾病索引不可用，未执行反查（不以合成数据顶替）",
                                           "blockers": [index.get("guidance", INDEX_GUIDANCE)],
                                           "assist": {"available": True, "guidance": INDEX_ASSIST},
                                           "artifacts": []}, directory)
                        return
                    database = discovery.database_path(ROOT)
                result, chunked = _reverse_lookup(database, genes)
                result["evidence_type"] = targets["evidence_type"]
                result["database"] = str(database)
                if chunked.get("chunked"):
                    result["chunking"] = chunked
            discovery.save_result(result, directory / "reverse")
            with LOCK:
                self.manifest.setdefault("verified_targets", {})[role] = str((directory / "reverse" / "result.json").relative_to(self.directory)).replace("\\", "/")
                self._reverse_metrics(result)
                self.save()
            summary = "反查命中 %d/%d 个靶点，候选疾病 %d 个（证据 %d 行）%s%s" % (
                result["matched_input_count"], result["input_count"],
                self.manifest["metrics"]["candidate_diseases"], self.manifest["metrics"]["evidence_rows"],
                "；输入超上限分 %d 块查询后合并" % chunked["chunks"] if chunked.get("chunked") else "",
                "（合成验证）" if self.manifest["mode"] == "fixture" else "")
            findings = []
            if self.manifest["mode"] == "fixture":
                findings.append("合成工程验证索引与靶点，非真实疾病关联")
            self.event(role, "tool.succeeded", summary)
            stage_result = {"status": "succeeded", "summary": summary, "blockers": [], "findings": findings,
                            "artifacts": ["reverse/result.json", "reverse/candidates.csv", "reverse/evidence.csv", "reverse/report.md", "reverse/manifest.json"]}
            stage_result = self._maybe_agent_review(role, stage_result, directory, {
                "input_count": result["input_count"], "matched_input_count": result["matched_input_count"],
                "chunking": result.get("chunking", {"chunked": False}),
                "candidates": [{"disease": c["disease"], "matched_count": c["matched_count"],
                                "input_coverage": c["input_coverage"], "disease_coverage": c["disease_coverage"],
                                "confidence": c.get("confidence"),
                                "evidence_rows": len(c["evidence"])} for c in result["candidates"]],
                "unmatched_genes_sample": result["unmatched_genes"][:30],
                "unmatched_total": len(result["unmatched_genes"])})
            self.finish(role, stage_result, directory)
        except Exception as exc:
            self.finish(role, {"status": "failed", "summary": str(exc), "blockers": [str(exc)], "artifacts": []}, directory)

    def _reverse_metrics(self, result):
        self.manifest["metrics"].update(
            matched_targets=result["matched_input_count"],
            candidate_diseases=sum(bool(candidate["matched_count"]) for candidate in result["candidates"]),
            evidence_rows=sum(len(candidate["evidence"]) for candidate in result["candidates"]),
            max_confidence=max((candidate.get("confidence", {}).get("value", 0.0)
                                for candidate in result["candidates"]), default=0.0))

    # ---- analysis 流水线：shared_targets → network → enrichment → analysis_review ----

    def _shared_genes(self):
        return read_json(self.directory / self.manifest["verified_targets"]["shared_targets"])

    def shared_targets_stage(self):
        """从来源 discovery 运行的靶点集与疾病索引求交集；空交集如实保留。"""
        role = "shared_targets"
        if self.reuse(role):
            with LOCK:
                stage = self.manifest["stages"][role]
                path = next(p for p in stage["artifacts"] if p.endswith("/shared_targets.json"))
                self.manifest.setdefault("verified_targets", {})[role] = path
                output = read_json(self.directory / path)
                self.manifest["metrics"].update(shared_targets=len(output["genes"]))
                self.save()
            return
        directory = self.begin(role)
        try:
            info = self.manifest["analysis"]
            source_dir = ROOT / "runs" / info["source_run_id"]
            source_manifest = read_json(source_dir / "manifest.json")
            source_refs = source_manifest.get("verified_targets", {})
            source_targets_rel = source_refs["herb_targets"]
            source_targets = read_json(source_dir / source_targets_rel)
            reverse_rel = source_refs["disease_reverse"]
            source_result = read_json(source_dir / reverse_rel)
            genes = source_targets["genes"]
            database, disease_genes = None, []
            if genes:
                database = source_result.get("database")
                recorded_hash = source_result.get("database_sha256")
                if not database or not Path(database).is_file():
                    # 来源未记录索引路径（旧运行）或文件已移动：回退当前配置解析
                    database = str(discovery.database_path(ROOT))
                    recorded_hash = None
                if recorded_hash and digest(database) != recorded_hash:
                    raise ValueError("来源运行记录的疾病索引哈希与当前文件不一致，请核对索引版本")
                with closing(discovery._connect(database)) as connection:
                    discovery._metadata(connection)
                    disease_genes = sorted(row[0] for row in connection.execute(
                        "SELECT DISTINCT gene FROM associations WHERE disease=?", (info["disease"],)))
            shared = sorted(set(genes) & set(disease_genes))
            output = {
                "evidence_type": source_targets.get("evidence_type"),
                "disease": info["disease"],
                "genes": shared,
                "counts": {"input_targets": len(genes), "disease_targets": len(disease_genes),
                           "shared": len(shared)},
                "source": {"run_id": info["source_run_id"],
                           "targets": source_targets_rel,
                           "targets_sha256": digest(source_dir / source_targets_rel),
                           "database": database,
                           "database_sha256": digest(database) if database else None},
            }
            write_json(directory / "shared_targets.json", output)
            with LOCK:
                self.manifest.setdefault("verified_targets", {})[role] = str((directory / "shared_targets.json").relative_to(self.directory)).replace("\\", "/")
                self.manifest["metrics"].update(shared_targets=len(shared))
                self.save()
            self.event(role, "tool.succeeded", "共同靶点 %d 个" % len(shared))
            self.finish(role, {"status": "succeeded",
                               "summary": "来源 %d 个靶点 ∩ 疾病「%s」的 %d 个关联基因 = %d 个共同靶点%s" % (
                                   len(genes), info["disease"], len(disease_genes), len(shared),
                                   "（合成验证）" if self.manifest["mode"] == "fixture" else ""),
                               "blockers": [], "findings": [], "artifacts": ["shared_targets.json"]}, directory)
        except Exception as exc:
            self.finish(role, {"status": "failed", "summary": str(exc), "blockers": [str(exc)], "artifacts": []}, directory)

    def network_stage(self):
        role = "network"
        if self.reuse(role):
            with LOCK:
                stage = self.manifest["stages"][role]
                path = next(p for p in stage["artifacts"] if p.endswith("/network.json"))
                self.manifest.setdefault("verified_targets", {})[role] = path
                network = read_json(self.directory / path)
                self.manifest["metrics"].update(network_nodes=network["node_count"],
                                                network_edges=network["edge_count"])
                self.save()
            return
        directory = self.begin(role)
        try:
            shared = self._shared_genes()
            genes = shared["genes"]
            if not genes:
                self.finish(role, {"status": "blocked", "summary": "共同靶点为空，未进行网络分析",
                                   "blockers": ["shared_targets 为空交集（如实记录），无网络输入"], "artifacts": []}, directory)
                return
            if self.manifest["mode"] == "fixture":
                net = {"nodes": list(genes),
                       "edges": [{"source": genes[i], "target": genes[i + 1], "score": 0.9}
                                 for i in range(len(genes) - 1)],
                       "provenance": {"synthetic": True, "note": "固定合成网络，非 STRING 查询"}}
            else:
                net = string_local.string_network_auto(genes, self.task, directory)
            result = network_metrics.analyze_network(net["nodes"], net["edges"])
            result["evidence_type"] = shared["evidence_type"]
            result["provenance"] = net.get("provenance", {})
            result["method"] = "NetworkX degree"
            limitation = None
            topology = self.task.get("network_topology")
            if self.manifest["mode"] == "live" and topology:
                try:
                    cyto = cytoscape.run_cytoscape(net, directory / "cytoscape", topology)
                    result["cytoscape"] = cyto
                    if cyto["status"] == "succeeded":
                        result["method"] = cyto["method"]
                        result["degree_table"] = result["degrees"] = cyto["degree_table"]
                    limitation = cyto.get("limitation")
                except Exception as exc:
                    limitation = "CytoNCA 桥未成功：" + str(exc)
                if limitation:
                    result["method"] = "NetworkX degree（CytoNCA 未完成，见 limitation）"
            write_json(directory / "network.json", result)
            with (directory / "degrees.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["gene_symbol", "degree"])
                writer.writeheader()
                writer.writerows(result["degree_table"])
            with LOCK:
                self.manifest.setdefault("verified_targets", {})[role] = str((directory / "network.json").relative_to(self.directory)).replace("\\", "/")
                self.manifest["metrics"].update(network_nodes=result["node_count"], network_edges=result["edge_count"])
                self.save()
            findings = []
            if self.manifest["mode"] == "fixture":
                findings.append("合成工程验证网络，非 STRING 查询结果")
            elif not topology:
                findings.append("任务未配置 network_topology；仅 NetworkX degree，未经 CytoNCA")
            self.event(role, "tool.succeeded", "网络 %d 节点 %d 边（%s）" % (result["node_count"], result["edge_count"], result["method"]))
            stage_result = {"status": "succeeded",
                            "summary": "网络 %d 节点 %d 边，度值方法：%s%s" % (
                                result["node_count"], result["edge_count"], result["method"],
                                "（合成验证）" if self.manifest["mode"] == "fixture" else ""),
                            "blockers": [], "findings": findings,
                            "artifacts": ["network.json", "degrees.csv",
                                          "cytoscape/cytoscape_execution.json", "cytoscape/cytonca_topology.csv"]}
            if limitation:
                stage_result["status"] = "partial"
                stage_result["blockers"] = [limitation]
            stage_result = self._maybe_agent_review(role, stage_result, directory, {
                "node_count": result["node_count"], "edge_count": result["edge_count"],
                "method": result["method"],
                "degree_top10": result["degree_table"][-10:],
                "unmapped": result["provenance"].get("unmapped"),
                "isolated": result["provenance"].get("isolated"),
                "limitation": limitation})
            self.finish(role, stage_result, directory)
        except Exception as exc:
            self.finish(role, {"status": "failed", "summary": str(exc), "blockers": [str(exc)],
                               "artifacts": ["network.json"] if (directory / "network.json").is_file() else []}, directory)

    def enrichment_stage(self):
        role = "enrichment"
        if self.reuse(role):
            with LOCK:
                stage = self.manifest["stages"][role]
                for path in stage["artifacts"]:
                    if path.endswith("/enrichment_fixture.json"):
                        self.manifest["metrics"]["significant_terms"] = read_json(self.directory / path)["significant_count"]
                    elif path.endswith("/david/enrichment_david.json"):
                        self.manifest["metrics"]["significant_terms"] = read_json(self.directory / path)["significant_count"]
                self.save()
            return
        directory = self.begin(role)
        try:
            shared = self._shared_genes()
            genes = shared["genes"]
            if not genes:
                self.finish(role, {"status": "blocked", "summary": "共同靶点为空，未进行富集分析",
                                   "blockers": ["shared_targets 为空交集（如实记录），无富集输入"], "artifacts": []}, directory)
                return
            if self.manifest["mode"] == "fixture":
                fdr = 0.05
                rows = [{"term": "SYNTHETIC_TERM_A（合成）", "overlap": min(2, len(genes)), "p_value": 0.01, "fdr_bh": 0.02},
                        {"term": "SYNTHETIC_TERM_B（合成）", "overlap": 1, "p_value": 0.4, "fdr_bh": 0.5}]
                execution = {"evidence_type": SYNTHETIC, "method": "固定合成富集结果，非 DAVID",
                             "fdr_lt": fdr, "rows": rows,
                             "significant_count": sum(r["fdr_bh"] < fdr for r in rows)}
                write_json(directory / "enrichment_fixture.json", execution)
                status, limitation = "succeeded", None
                artifacts = ["enrichment_fixture.json"]
                method_note = "固定合成富集结果，非 DAVID"
            else:
                execution = david.run_david(genes, self.task, directory / "david")
                status = execution["status"]
                limitation = execution.get("limitation")
                if "significant_count" in execution:
                    with LOCK:
                        self.manifest["metrics"]["significant_terms"] = execution["significant_count"]
                        self.save()
                artifacts = ["david/david_execution.json", "david/david_all_terms.csv",
                             "david/david_significant_terms.csv", "david/enrichment_david.json",
                             "david/david_input.json", "david/david_artifacts.json"]
                method_note = execution.get("method", "DAVID")
            if self.manifest["mode"] == "fixture":
                with LOCK:
                    self.manifest["metrics"]["significant_terms"] = execution["significant_count"]
                    self.save()
            summary = ("富集显著 %d 条（%s）" % (execution.get("significant_count", 0), method_note)
                       if status == "succeeded" else "富集未完成：" + str(limitation or status))
            stage_result = {"status": status, "summary": summary, "blockers": [limitation] if limitation else [],
                            "findings": ["合成工程验证富集，非 DAVID 结果"] if self.manifest["mode"] == "fixture" else [],
                            "artifacts": artifacts}
            stage_result = self._maybe_agent_review(role, stage_result, directory, {
                "genes_total": len(genes), "method": method_note, "status": status,
                "significant_count": execution.get("significant_count"),
                "mapped_count": execution.get("mapped_count"),
                "limitation": limitation})
            self.finish(role, stage_result, directory)
        except Exception as exc:
            self.finish(role, {"status": "failed", "summary": str(exc), "blockers": [str(exc)], "artifacts": []}, directory)

    def analysis_review_stage(self):
        role = "analysis_review"
        directory = self.begin(role)
        problems = self._verify_analysis()
        write_json(directory / "verification.json", {"checks": "artifacts_sha256_and_count_consistency", "problems": problems})
        self._write_analysis_report(problems)
        if problems:
            result = {"status": "failed", "summary": "验收发现 %d 个问题" % len(problems),
                      "blockers": problems, "artifacts": ["verification.json"]}
        else:
            result = {"status": "succeeded", "summary": "机制分析链路产物核对通过",
                      "blockers": [], "artifacts": ["verification.json"]}
        result = self._maybe_agent_review(role, result, directory, {
            "problems": problems,
            "metrics": self.manifest.get("metrics", {}),
            "stages": {name: {"status": stage.get("status"), "summary": stage.get("summary")}
                       for name, stage in self.manifest["stages"].items() if name != role},
            "artifact_index": {path: (stage.get("artifact_sha256") or {}).get(path)
                               for name, stage in self.manifest["stages"].items() if name != role
                               for path in stage.get("artifacts", [])}})
        self.finish(role, result, directory)

    def _verify_analysis(self):
        problems = []
        stages = self.manifest["stages"]
        for role in ("shared_targets", "network", "enrichment"):
            stage = stages.get(role, {})
            if stage.get("status") != "succeeded":
                continue
            for relative, expected in (stage.get("artifact_sha256") or {}).items():
                path = self.directory / relative
                if not path.is_file():
                    problems.append("%s 产物缺失：%s" % (role, relative))
                elif digest(path) != expected:
                    problems.append("%s 产物哈希不一致：%s" % (role, relative))
        metrics = self.manifest.get("metrics", {})
        shared_stage = stages.get("shared_targets", {})
        if shared_stage.get("status") == "succeeded":
            shared = self._shared_genes()
            if metrics.get("shared_targets") != len(shared["genes"]):
                problems.append("共同靶点计数不一致：metrics=%s shared_targets.json=%d"
                                % (metrics.get("shared_targets"), len(shared["genes"])))
            if self.manifest["mode"] == "fixture" and shared.get("evidence_type") != SYNTHETIC:
                problems.append("fixture 运行共同靶点未标注 synthetic_engineering")
        if stages.get("network", {}).get("status") in ("succeeded", "partial"):
            network = read_json(self.directory / self.manifest["verified_targets"]["network"])
            if metrics.get("network_nodes") != network["node_count"]:
                problems.append("网络节点计数不一致：metrics=%s network.json=%d"
                                % (metrics.get("network_nodes"), network["node_count"]))
            if self.manifest["mode"] == "fixture" and network.get("evidence_type") != SYNTHETIC:
                problems.append("fixture 运行网络未标注 synthetic_engineering")
        if stages.get("enrichment", {}).get("status") == "succeeded" and self.manifest["mode"] == "fixture":
            fixture = read_json(self.directory / "enrichment" / ("attempt_%02d" % stages["enrichment"]["attempt"]) / "enrichment_fixture.json")
            if metrics.get("significant_terms") != fixture["significant_count"]:
                problems.append("显著条目计数不一致：metrics=%s enrichment_fixture.json=%d"
                                % (metrics.get("significant_terms"), fixture["significant_count"]))
        return problems

    def _write_analysis_report(self, problems):
        task, manifest = self.task, self.manifest
        info = manifest.get("analysis", {})
        fixture = manifest["mode"] == "fixture"
        metrics = manifest.get("metrics", {})
        lines = ["# 机制分析运行报告", "",
                 "运行：" + self.run_id, "",
                 "来源运行：" + str(info.get("source_run_id")) + "；疾病关键词：" + str(info.get("disease")), "",
                 "模式：" + ("fixture（合成工程验证，不是药理研究结果）" if fixture else "live（本地数据分析）"), "",
                 "状态：" + manifest["status"], "",
                 "scientific_complete：false（关联、网络与富集均不构成机制结论）", "",
                 "案例：" + (task.get("formula") or "（自由药材组合）") + "（" + "、".join(task.get("herbs", [])) + "）", "",
                 "## 阶段概览", "", "| 阶段 | 状态 | 摘要 |", "| --- | --- | --- |"]
        for role in self.stages:
            stage = manifest["stages"][role]
            lines.append("| %s | %s | %s |" % (self.labels[role], stage["status"], stage.get("summary", "")))
        lines.append("")
        lines.extend(["## 结果概览", "",
                      "共同靶点：%d" % metrics.get("shared_targets", 0), ""])
        if metrics.get("network_nodes") is not None:
            lines.append("网络：%d 节点 / %d 边" % (metrics.get("network_nodes", 0), metrics.get("network_edges", 0)))
            network_path = manifest.get("verified_targets", {}).get("network")
            if network_path and (self.directory / network_path).is_file():
                network = read_json(self.directory / network_path)
                lines.append("度值方法：" + network.get("method", ""))
                top = sorted(network["degree_table"], key=lambda r: (-r["degree"], r["gene_symbol"]))[:10]
                if top:
                    lines.extend(["", "Degree Top：", ""])
                    lines.extend("- %s：%d" % (row["gene_symbol"], row["degree"]) for row in top)
                lines.append("")
        if metrics.get("significant_terms") is not None:
            lines.extend(["富集显著条目：%d" % metrics["significant_terms"], ""])
        if problems:
            lines.extend(["## 验收问题", ""])
            lines.extend("- " + problem for problem in problems)
            lines.append("")
        lines.extend(["## 局限声明", "",
                      "疾病关联、网络拓扑与富集结果均不构成机制或疗效结论；scientific_complete 恒为 false。"
                      + ("本运行为合成工程验证，全部数据为固定测试集合。" if fixture else "")])
        (self.directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with LOCK:
            self.manifest["report"] = "report.md"
            self.save()

    def review_stage(self):
        role = "review"
        directory = self.begin(role)
        problems = self._verify()
        write_json(directory / "verification.json", {"checks": "artifacts_sha256_and_count_consistency", "problems": problems})
        self.write_report(problems)
        if problems:
            result = {"status": "failed", "summary": "验收发现 %d 个问题" % len(problems),
                      "blockers": problems, "artifacts": ["verification.json"]}
        else:
            result = {"status": "succeeded", "summary": "产物存在性、哈希与计数一致性核对通过",
                      "blockers": [], "artifacts": ["verification.json"]}
        result = self._maybe_agent_review(role, result, directory, {
            "problems": problems,
            "metrics": self.manifest.get("metrics", {}),
            "stages": {name: {"status": stage.get("status"), "summary": stage.get("summary")}
                       for name, stage in self.manifest["stages"].items() if name != role},
            "artifact_index": {path: (stage.get("artifact_sha256") or {}).get(path)
                               for name, stage in self.manifest["stages"].items() if name != role
                               for path in stage.get("artifacts", [])},
            "coordinator_opening": self.manifest.get("coordinator_opening")})
        self.finish(role, result, directory)

    def _verify(self):
        """核对成功阶段的产物存在性/哈希、跨阶段计数与 provenance 完整性。"""
        problems = []
        stages = self.manifest["stages"]
        for role in ("preflight", "herb_targets", "disease_reverse"):
            stage = stages.get(role, {})
            if stage.get("status") != "succeeded":
                continue
            for relative, expected in (stage.get("artifact_sha256") or {}).items():
                path = self.directory / relative
                if not path.is_file():
                    problems.append("%s 产物缺失：%s" % (role, relative))
                elif digest(path) != expected:
                    problems.append("%s 产物哈希不一致：%s" % (role, relative))
        herb = stages.get("herb_targets", {})
        reverse = stages.get("disease_reverse", {})
        metrics = self.manifest.get("metrics", {})
        if herb.get("status") == "succeeded":
            targets = read_json(self.directory / self.manifest["verified_targets"]["herb_targets"])
            if metrics.get("unique_targets") != len(targets["genes"]):
                problems.append("靶点计数不一致：metrics=%s targets.json=%d" % (metrics.get("unique_targets"), len(targets["genes"])))
            provenance = targets.get("provenance", {})
            if self.manifest["mode"] == "fixture":
                if targets.get("evidence_type") != SYNTHETIC:
                    problems.append("fixture 运行靶点未标注 synthetic_engineering")
            elif not all("sha256" in entry for entry in provenance.get("files", {}).values()) or not provenance.get("files"):
                problems.append("targets.json provenance 缺少 BATMAN 文件哈希记录")
            if reverse.get("status") == "succeeded":
                result = read_json(self.directory / self.manifest["verified_targets"]["disease_reverse"])
                if result["input_count"] != len(targets["genes"]):
                    problems.append("反查输入数与靶点数不一致：%d != %d" % (result["input_count"], len(targets["genes"])))
                evidence_rows = sum(len(candidate["evidence"]) for candidate in result["candidates"])
                if metrics.get("evidence_rows") != evidence_rows:
                    problems.append("证据行数不一致：metrics=%s result.json=%d" % (metrics.get("evidence_rows"), evidence_rows))
                if self.manifest["mode"] == "fixture" and result.get("evidence_type") != SYNTHETIC:
                    problems.append("fixture 运行反查结果未标注 synthetic_engineering")
        return problems

    def write_report(self, problems):
        task, manifest = self.task, self.manifest
        fixture = manifest["mode"] == "fixture"
        lines = ["# 方剂反向疾病发现运行报告", "",
                 "运行：" + self.run_id, "",
                 "模式：" + ("fixture（合成工程验证，不是药理研究结果）" if fixture else "live（本地数据反查）"), "",
                 "状态：" + manifest["status"], "",
                 "scientific_complete：false（关键词关联≠疗效；未做人工科学核验）", "",
                 "## 输入", "",
                 "方剂：" + (task.get("formula") or "（自由药材组合）"), "",
                 "药材：" + "、".join(task["herbs"]), "",
                 "BATMAN 阈值：" + str(task.get("batman_threshold", 0.84)), ""]
        if task.get("research_notes"):
            lines.extend(["研究说明：" + task["research_notes"], ""])
        if not fixture:
            if self._agents:
                opening = manifest.get("coordinator_opening") or {}
                lines.extend(["多 Agent 核验：已启用（协调开场：" + str(opening.get("status", "未执行")) + "；各阶段核验结论见 handoff 的 agent_review）", ""])
            else:
                lines.extend(["多 Agent 核验：未启用（agents=false），本运行为纯程序流水线，无 Agent 核验", ""])
        lines.extend(["## 阶段概览", "", "| 阶段 | 状态 | 摘要 |", "| --- | --- | --- |"])
        for role in self.stages:
            stage = manifest["stages"][role]
            lines.append("| %s | %s | %s |" % (self.labels[role], stage["status"], stage.get("summary", "")))
        lines.append("")
        reverse_path = manifest.get("verified_targets", {}).get("disease_reverse")
        if reverse_path and (self.directory / reverse_path).is_file():
            result = read_json(self.directory / reverse_path)
            if result.get("candidates"):
                lines.extend(["## 候选疾病（关键词关联，不是疗效排名）", "",
                              "| 疾病关键词 | 置信度 | 匹配靶点数 | 输入覆盖率 | 库内该病靶点数 |", "| --- | ---: | ---: | ---: | ---: |"])
                for candidate in result["candidates"]:
                    coverage = ("%.2f%%" % (candidate["input_coverage"] * 100)) if result["input_count"] else "—"
                    confidence = candidate.get("confidence", {}).get("value")
                    lines.append("| %s | %s | %d | %s | %d |" % (
                        candidate["disease"],
                        confidence if confidence is not None else "—",
                        candidate["matched_count"], coverage, candidate["indexed_target_count"]))
                lines.extend(["", "置信度为程序计算的透明启发式（" + discovery.CONFIDENCE_VERSION + "），非统计检验，仅供排序参考；组件明细见 reverse/result.json。",
                              "", "未匹配靶点：" + (", ".join(result["unmatched_genes"]) or "无"), ""])
        herb_path = manifest.get("verified_targets", {}).get("herb_targets")
        if herb_path and (self.directory / herb_path).is_file():
            targets = read_json(self.directory / herb_path)
            if targets.get("unmatched_herbs"):
                lines.extend(["BATMAN 未收录或未命中药材：" + "、".join(targets["unmatched_herbs"]), ""])
        if problems:
            lines.extend(["## 验收问题", ""])
            lines.extend("- " + problem for problem in problems)
            lines.append("")
        lines.extend(["## 局限声明", "", discovery.LIMITATION, "",
                      "本报告由确定性程序生成；空结果如实保留，未调整阈值凑数。" + ("本运行为合成工程验证，全部数据为固定测试集合。" if fixture else "")])
        (self.directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with LOCK:
            self.manifest["report"] = "report.md"
            self.save()

    def run(self):
        self.manifest["status"] = "running"
        self.manifest["scientific_complete"] = False
        self.manifest.pop("finished_at", None)
        self.manifest["verified_targets"] = {}
        self.manifest["metrics"] = {}
        self.save()
        try:
            if self._agents:
                ok, error = self._check_agents()
                if not ok:
                    self.manifest["status"] = "failed"
                    self.manifest["fatal_error"] = ("多 Agent 模式需要可用的 Codex 环境：" + error
                                                    + "；核验环境后将任务 agents 保持 true 重试，或设 agents=false 仅用程序流水线")
                    self.event("system", "agents.unavailable", error)
                    for stage in self.manifest["stages"].values():
                        stage.update(status="skipped", summary="多 Agent 环境不可用，未执行", finished_at=now())
                    self.write_report([self.manifest["fatal_error"]])
                    return
            if self.pipeline == "analysis":
                stage_map = {"shared_targets": self.shared_targets_stage,
                             "network": self.network_stage,
                             "enrichment": self.enrichment_stage,
                             "analysis_review": self.analysis_review_stage}
            else:
                stage_map = {"preflight": self.preflight_stage,
                             "herb_targets": self._herb_stage_with_opening,
                             "disease_reverse": self.disease_reverse_stage,
                             "review": self.review_stage}
            execute_graph(lambda name: stage_map[name](), max_workers=1, pipeline=self.pipeline)
            statuses = [stage["status"] for stage in self.manifest["stages"].values()]
            if any(status == "failed" for status in statuses):
                self.manifest["status"] = "failed"
            elif any(status in ("blocked", "partial") for status in statuses):
                self.manifest["status"] = "partial"
            else:
                self.manifest["status"] = "succeeded"
            self.manifest["scientific_complete"] = False
        except Exception as exc:
            self.manifest["status"] = "failed"
            self.manifest["fatal_error"] = str(exc)
            self.event("system", "run.failed", str(exc))
        finally:
            self.manifest["finished_at"] = now()
            for role, stage in self.manifest["stages"].items():
                if stage["status"] == "running":
                    stage.update(status="failed", summary="运行提前结束，保留现场供重试", finished_at=now())
            self.save()
            if self.manifest.get("mode") == "live":
                try:
                    self.manifest["archive"] = archive_run(self.directory, ROOT / "data" / "pharm")
                except (ValueError, OSError, KeyError) as exc:
                    self.manifest["archive_error"] = str(exc)
                self.save()
            self.event("system", "run.completed", "运行结束：" + self.manifest["status"])


def manifests():
    out = []
    workspace_id = workspace_identity(ROOT)["workspace_id"]
    for file in (ROOT / "runs").glob("*/manifest.json"):
        try:
            value = read_json(file)
            if value.get("workspace_id") != workspace_id:
                continue
            if value.get("status") == "running" and value["run_id"] not in ACTIVE:
                # Could be another CLI process; do not guess or mutate persisted state.
                value["note"] = "运行由另一进程执行或已中断；确认进程后可恢复。"
            out.append(value)
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


def _analysis_task(analysis, mode):
    """从来源 discovery 运行合成 analysis 任务快照；校验来源与疾病合法性。"""
    if not isinstance(analysis, dict):
        raise ValueError("analysis 请求必须是 JSON 对象")
    allowed = {"discovery_run_id", "disease", "research_notes", "agents",
               "string_source", "string_local_dir", "network_topology", "david_enrichment"}
    if set(analysis) - allowed:
        raise ValueError("包含不支持的 analysis 字段：" + ", ".join(sorted(set(analysis) - allowed)))
    run_id = safe_name(str(analysis.get("discovery_run_id") or ""))
    source_dir = ROOT / "runs" / run_id
    try:
        source_manifest = read_json(source_dir / "manifest.json")
    except (OSError, ValueError):
        raise ValueError("来源运行不存在：" + run_id) from None
    if not owned_run(source_manifest, ROOT):
        raise ValueError("来源运行属于旧数据或其他工作区，不能开展机制分析")
    if source_manifest.get("pipeline", "discovery") != "discovery":
        raise ValueError("来源运行不是 discovery 流水线")
    stage = source_manifest.get("stages", {}).get("disease_reverse", {})
    if stage.get("status") != "succeeded":
        raise ValueError("来源运行的疾病反查未成功，不能开展机制分析")
    rel = source_manifest.get("verified_targets", {}).get("disease_reverse")
    if not rel:
        raise ValueError("来源运行缺少反查产物索引")
    result = read_json(source_dir / rel)
    disease = analysis.get("disease")
    if disease not in [c["disease"] for c in result.get("candidates", [])]:
        raise ValueError("疾病不在来源运行的候选清单内：" + str(disease))
    mode = mode or source_manifest.get("mode") or "live"  # 缺省继承来源运行模式，保持证据类型一致
    if mode not in ("live", "fixture"):
        raise ValueError("不支持的运行模式")
    agents = analysis.get("agents")
    if agents is None:
        agents = mode == "live"
    if not isinstance(agents, bool):
        raise ValueError("agents 须为布尔值")
    if mode == "fixture":
        agents = False
    if "string_source" in analysis and analysis["string_source"] not in ("api", "local_files"):
        raise ValueError("string_source 只支持 api 或 local_files")
    for key in ("network_topology", "david_enrichment"):
        if key in analysis and not isinstance(analysis[key], dict):
            raise ValueError(key + " 须为对象")
    source_task = source_manifest.get("task", {})
    task = {
        "formula": source_task.get("formula"), "herbs": source_task.get("herbs", []),
        "research_notes": str(analysis.get("research_notes") or ""),
        "batman_threshold": source_task.get("batman_threshold", 0.84),
        "mode": mode, "composition": source_task.get("composition", "custom_herbs"),
        "agents": agents,
        # STRING/DAVID 的默认研究参数：物种人、0.9 置信度、无额外节点、v12.0
        "taxon_id": 9606, "string_confidence": 0.9, "string_additional_nodes": 0,
        "string_version": "12.0",
        "analysis": {"source_run_id": run_id, "disease": disease},
    }
    for key in ("string_source", "string_local_dir", "network_topology", "david_enrichment"):
        if key in analysis:
            task[key] = analysis[key]
    task["task_id"] = "analysis_" + hashlib.sha256(
        json.dumps(task, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return task


def start(task_id=None, mode=None, resume=None, background=True, pipeline="discovery", analysis=None):
    if mode not in (None, "live", "fixture"):
        raise ValueError("不支持的运行模式")
    if pipeline not in scheduler.GRAPHS:
        raise ValueError("未注册的流水线：" + str(pipeline))
    (ROOT / "runs").mkdir(exist_ok=True)
    lockfile = ROOT / "runs/.runner.lock"
    try:
        descriptor = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("已有运行锁；请等待当前任务结束。若进程已中断，请先核验锁文件中的进程号。")
    os.write(descriptor, json.dumps({"pid": os.getpid(), "created_at": now()}).encode())
    os.close(descriptor)
    try:
        if resume:
            run_id = safe_name(resume)
            manifest = read_json(ROOT / "runs" / run_id / "manifest.json")
            if manifest.get("workflow_version") != WORKFLOW_VERSION:
                raise ValueError("旧结构运行不能由新流水线恢复，请新建运行")
            if not owned_run(manifest, ROOT):
                raise ValueError("运行属于旧数据或其他工作区，不能恢复；请新建运行")
            # Successful verified stages can be reused; all other stages get new attempts.
        else:
            if pipeline == "analysis":
                task = _analysis_task(analysis, mode)
                mode = task["mode"]
            else:
                tasks = task_list()
                task = next((task for task in tasks if task["task_id"] == task_id), None)
                if task is None:
                    raise ValueError("未知 task_id")
                mode = mode or task.get("mode", "live")
            labels = PIPELINE_LABELS[pipeline]
            run_id = now().replace(":", "").replace("+", "_") + "_" + uuid.uuid4().hex[:6]
            run_id = run_id.replace(".", "_")
            directory = ROOT / "runs" / run_id
            directory.mkdir()
            manifest = {"run_id": run_id, "workspace_id": workspace_identity(ROOT)["workspace_id"],
                        "task": task, "mode": mode, "pipeline": pipeline, "status": "pending",
                        "created_at": now(), "runtime": read_json(ROOT / "configs/runtime.json"),
                        "scientific_complete": False, "workflow_version": WORKFLOW_VERSION,
                        "stages": {role: {"label": labels[role], "status": "pending",
                                          "summary": "等待调度", "blockers": [], "artifacts": []}
                                   for role in scheduler.stages_for(pipeline)}}
            if pipeline == "analysis":
                manifest["analysis"] = task["analysis"]
            write_json(directory / "manifest.json", manifest)
        ACTIVE.add(run_id)
        def worker():
            try:
                Runner(run_id).run()
            finally:
                ACTIVE.discard(run_id)
                lockfile.unlink(missing_ok=True)
        if background:
            threading.Thread(target=worker, name="pharm-runner", daemon=False).start()
        else:
            worker()
        return run_id
    except Exception:
        lockfile.unlink(missing_ok=True)
        raise
