"""四阶段反向发现流水线：preflight → herb_targets → disease_reverse → review。

纯程序确定性执行，无模型会话；本地数据缺失时阶段置 blocked 并给出升级点
指引，不合成顶替（fixture 模式除外，其产物一律标注 synthetic_engineering）。
manifest.json 是唯一状态源；attempt_NN 不可变；resume 只复用签名与产物
哈希一致的成功阶段。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path

from ..core.common import ROOT, digest, now, read_json, safe_name, task_list, write_json, public_artifact
from ..core.archive import archive_run
from ..batman import local as batman_local
from ..batman.formulas import resolve_batman_names
from ..discovery import query as discovery
from .scheduler import STAGES, execute_graph

WORKFLOW_VERSION = 3
LABELS = dict(zip(STAGES, ["本地数据预检", "药材靶点解析", "疾病反向查询", "程序验收与报告"]))
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
            self.manifest["stages"][role] = {"status": "running", "label": LABELS[role], "summary": "正在执行", "blockers": [], "artifacts": [], "attempt": attempt, "input_signature": self.signature(role), "started_at": now(), "directory": str(directory.relative_to(self.directory)).replace("\\", "/")}
            self.save()
        self.event(role, "stage.started", "开始第 %d 次尝试" % attempt)
        return directory

    def signature(self, role):
        inputs = {"task": self.task, "mode": self.manifest["mode"], "role": role,
                  "runtime": read_json(ROOT / "configs/runtime.json"),
                  "code": {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted((ROOT / "pharm").rglob("*.py"))}}
        if self.manifest["mode"] == "fixture":
            inputs["fixture"] = SYNTHETIC
        else:
            if role in ("preflight", "herb_targets"):
                inputs["batman_data"] = self._batman_signature()
            if role in ("disease_reverse", "review"):
                try:
                    database = discovery.database_path(ROOT)
                    inputs["discovery_db"] = digest(database) if database.is_file() else None
                except ValueError as exc:
                    inputs["discovery_db"] = {"unavailable": str(exc)}
        upstream = {"disease_reverse": ["herb_targets"], "review": ["herb_targets", "disease_reverse"]}.get(role, [])
        inputs["upstream"] = {key: digest(self.directory / path)
                              for key, path in self.manifest.get("verified_targets", {}).items()
                              if key in upstream and (self.directory / path).is_file()}
        return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

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
            self.finish(role, {
                "status": "succeeded",
                "summary": "%d 味药材解析出 %d 个唯一靶点（关系 %d 行）%s" % (
                    len(output["herbs"]), len(output["genes"]), len(output["relations"]),
                    "（合成验证）" if self.manifest["mode"] == "fixture" else ""),
                "blockers": [], "findings": findings, "artifacts": ["targets.json"],
            }, directory)
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
            self.finish(role, {"status": "succeeded", "summary": summary, "blockers": [], "findings": findings,
                               "artifacts": ["reverse/result.json", "reverse/candidates.csv", "reverse/evidence.csv", "reverse/report.md", "reverse/manifest.json"]},
                        directory)
        except Exception as exc:
            self.finish(role, {"status": "failed", "summary": str(exc), "blockers": [str(exc)], "artifacts": []}, directory)

    def _reverse_metrics(self, result):
        self.manifest["metrics"].update(
            matched_targets=result["matched_input_count"],
            candidate_diseases=sum(bool(candidate["matched_count"]) for candidate in result["candidates"]),
            evidence_rows=sum(len(candidate["evidence"]) for candidate in result["candidates"]))

    def review_stage(self):
        role = "review"
        directory = self.begin(role)
        problems = self._verify()
        write_json(directory / "verification.json", {"checks": "artifacts_sha256_and_count_consistency", "problems": problems})
        self.write_report(problems)
        if problems:
            self.finish(role, {"status": "failed", "summary": "验收发现 %d 个问题" % len(problems),
                               "blockers": problems, "artifacts": ["verification.json"]}, directory)
        else:
            self.finish(role, {"status": "succeeded", "summary": "产物存在性、哈希与计数一致性核对通过",
                               "blockers": [], "artifacts": ["verification.json"]}, directory)

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
        lines.extend(["## 阶段概览", "", "| 阶段 | 状态 | 摘要 |", "| --- | --- | --- |"])
        for role in STAGES:
            stage = manifest["stages"][role]
            lines.append("| %s | %s | %s |" % (LABELS[role], stage["status"], stage.get("summary", "")))
        lines.append("")
        reverse_path = manifest.get("verified_targets", {}).get("disease_reverse")
        if reverse_path and (self.directory / reverse_path).is_file():
            result = read_json(self.directory / reverse_path)
            if result.get("candidates"):
                lines.extend(["## 候选疾病（关键词关联，不是疗效排名）", "",
                              "| 疾病关键词 | 匹配靶点数 | 输入覆盖率 | 库内该病靶点数 |", "| --- | ---: | ---: | ---: |"])
                for candidate in result["candidates"]:
                    coverage = ("%.2f%%" % (candidate["input_coverage"] * 100)) if result["input_count"] else "—"
                    lines.append("| %s | %d | %s | %d |" % (candidate["disease"], candidate["matched_count"], coverage, candidate["indexed_target_count"]))
                lines.extend(["", "未匹配靶点：" + (", ".join(result["unmatched_genes"]) or "无"), ""])
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
            stage_map = {"preflight": self.preflight_stage,
                         "herb_targets": self.herb_targets_stage,
                         "disease_reverse": self.disease_reverse_stage,
                         "review": self.review_stage}
            execute_graph(lambda name: stage_map[name](), max_workers=1)
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
    for file in (ROOT / "runs").glob("*/manifest.json"):
        try:
            value = read_json(file)
            if value.get("status") == "running" and value["run_id"] not in ACTIVE:
                # Could be another CLI process; do not guess or mutate persisted state.
                value["note"] = "运行由另一进程执行或已中断；确认进程后可恢复。"
            out.append(value)
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


def start(task_id=None, mode=None, resume=None, background=True):
    if mode not in (None, "live", "fixture"):
        raise ValueError("不支持的运行模式")
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
            # Successful verified stages can be reused; all other stages get new attempts.
        else:
            tasks = task_list()
            task = next((task for task in tasks if task["task_id"] == task_id), None)
            if task is None:
                raise ValueError("未知 task_id")
            mode = mode or task.get("mode", "live")
            run_id = now().replace(":", "").replace("+", "_") + "_" + uuid.uuid4().hex[:6]
            run_id = run_id.replace(".", "_")
            directory = ROOT / "runs" / run_id
            directory.mkdir()
            manifest = {"run_id": run_id, "task": task, "mode": mode, "status": "pending", "created_at": now(), "runtime": read_json(ROOT / "configs/runtime.json"), "scientific_complete": False, "workflow_version": WORKFLOW_VERSION, "stages": {role: {"label": LABELS[role], "status": "pending", "summary": "等待调度", "blockers": [], "artifacts": []} for role in STAGES}}
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
