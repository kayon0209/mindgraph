import json
from pathlib import Path

import pytest

from evaluation.mindgraph_retrieval_eval import (
    DEFAULT_CANDIDATE_DATASET_PATH,
    dataset_sha256,
    evaluate_retrieval_cases,
    load_candidate_dataset,
    load_golden_dataset,
    validate_candidate_cases,
    validate_golden_cases,
)
from src.retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


def case(case_id="A", behavior="answer", paths=None, question="q"):
    return {
        "case_id": case_id,
        "question": question,
        "category": "test",
        "split": "development",
        "expected_behavior": behavior,
        "gold_vault_paths": paths if paths is not None else ["gold.md"],
        "required_facts": ["required"] if behavior == "answer" else [],
        "forbidden_facts": [],
        "dataset_version": "test-v1",
        "label_source": "human-authored-test-fixture",
        "query_type": "exact_fact",
        "difficulty": "easy",
        "expected_route": "factual",
        "graph_needed": False,
        "acl_context": {},
        "source": "human-authored-test-fixture",
        "validation_status": "approved",
        "notes": "test fixture",
    }


def candidate_case(case_id="C", behavior="answer", paths=None, question="q"):
    return {
        "case_id": case_id,
        "question": question,
        "category": "candidate",
        "query_type": "versioned_policy",
        "split": "development",
        "expected_behavior": behavior,
        "gold_vault_paths": paths if paths is not None else ["gold.md"],
        "required_facts": ["required"] if behavior == "answer" else [],
        "forbidden_facts": [],
        "dataset_version": "candidate-v1",
        "label_source": "generated-candidate-test-fixture",
        "source": "generated_candidate",
        "validation_status": "pending",
        "difficulty": "easy",
        "notes": "test fixture",
        "expected_route": "hybrid",
        "graph_needed": False,
        "acl_context": {"roles": ["employee"]},
    }


def candidate(path):
    chunk = Chunk(path, "text", "doc", 0, None, {"vault_path": path})
    return RetrievalCandidate(chunk=chunk)


def trace(*stages):
    names = ("dense_results", "sparse_results", "fused_results", "reranked_results", "final_selected_chunks")
    value = RetrievalTrace("q", "hybrid", "hybrid")
    for name, paths in zip(names, stages, strict=True):
        setattr(value, name, [candidate(path) for path in paths])
    return value


def test_load_and_validate_jsonl_and_locate_errors(tmp_path: Path):
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps(case(), ensure_ascii=False) + "\n", encoding="utf-8")
    assert load_golden_dataset(path)[0]["case_id"] == "A"

    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text("\n".join(json.dumps(case(case_id="A")) for _ in range(2)), encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 2.*A"):
        load_golden_dataset(duplicate)

    with pytest.raises(ValueError, match="B"):
        validate_golden_cases([case(), case(case_id="B", behavior="abstain", paths=["bad.md"])])

    missing_version = case("missing-version")
    missing_version.pop("dataset_version")
    with pytest.raises(ValueError, match=r"missing-version.*dataset_version"):
        validate_golden_cases([missing_version])
    other_version = case("other-version")
    other_version["dataset_version"] = "test-v2"
    with pytest.raises(ValueError, match=r"other-version.*consistent"):
        validate_golden_cases([case(), other_version])


def test_rejects_empty_dataset_before_reporting(tmp_path: Path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")

    with pytest.raises(ValueError, match="at least one case"):
        load_golden_dataset(empty)
    with pytest.raises(ValueError, match="at least one case"):
        validate_golden_cases([])
    with pytest.raises(ValueError, match="at least one case"):
        evaluate_retrieval_cases([], lambda _case: trace([], [], [], [], []))


@pytest.mark.parametrize("top_k", [0, -1, 1.5, True])
def test_rejects_invalid_top_k_before_retrieval(top_k):
    called = False

    def retrieve(_case):
        nonlocal called
        called = True
        return trace([], [], [], [], [])

    with pytest.raises(ValueError, match="positive integer"):
        evaluate_retrieval_cases([case()], retrieve, top_k=top_k)
    assert called is False


@pytest.mark.parametrize(
    "field",
    [
        "case_id",
        "question",
        "category",
        "split",
        "expected_behavior",
        "gold_vault_paths",
        "required_facts",
        "forbidden_facts",
        "dataset_version",
        "label_source",
    ],
)
def test_v2_contract_requires_every_core_field(field):
    value = case()
    value.pop(field)

    with pytest.raises(ValueError, match=field):
        validate_golden_cases([value])


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    [
        ("case_id", 1, "case_id"),
        ("question", 1, "question"),
        ("category", [], "category"),
        ("split", "holdout", "split"),
        ("split", [], "split"),
        ("expected_behavior", "refuse", "expected_behavior"),
        ("expected_behavior", [], "expected_behavior"),
        ("gold_vault_paths", "gold.md", "gold_vault_paths"),
        ("required_facts", "fact", "required_facts"),
        ("forbidden_facts", [1], "forbidden_facts"),
        ("dataset_version", 2, "dataset_version"),
        ("label_source", "", "label_source"),
    ],
)
def test_v2_contract_validates_types_and_enums(field, invalid, message):
    value = case()
    value[field] = invalid

    with pytest.raises(ValueError, match=message):
        validate_golden_cases([value])


def test_v2_contract_enforces_behavior_specific_evidence_rules():
    answer_without_facts = case()
    answer_without_facts["required_facts"] = []
    with pytest.raises(ValueError, match=r"answer.*required_facts"):
        validate_golden_cases([answer_without_facts])

    abstain = case("abstain", behavior="abstain", paths=[])
    abstain["required_facts"] = []
    abstain["forbidden_facts"] = []
    assert validate_golden_cases([abstain]) == [abstain]

    abstain["gold_vault_paths"] = ["gold.md"]
    with pytest.raises(ValueError, match=r"abstain.*gold_vault_paths"):
        validate_golden_cases([abstain])


def test_repository_v2_golden_dataset_is_valid_and_structurally_complete():
    """The checked-in V2 set remains an independently authored, typed fixture."""
    cases = load_golden_dataset()

    assert len(cases) == 90  # 54 (v2.3.0) + 36 plan-coverage gap fill (2026-08-27)
    assert {item["dataset_version"] for item in cases} == {"2.4.0"}
    assert {item["expected_behavior"] for item in cases} == {"answer", "abstain"}
    assert {item["split"] for item in cases} == {"development", "regression"}
    assert {item["category"] for item in cases} >= {
        "version",
        "supersession",
        "exception",
        "cross_policy",
        "no_answer",
        "ambiguity",
        "graph_needed",
        "graph_control",
    }
    assert {item["query_type"] for item in cases} >= {
        "exact_fact",
        "multi_condition",
        "versioned_policy",
        "no_answer",
        "exception",
        "conflict",
        "acl_restricted",
        "synonym_abbrev",
        "graph_needed",
        "graph_control",
    }
    assert all(
        item["label_source"] in {
            "human-authored-from-demo-vault",
            "human-validated-from-demo-vault",
            "human-validated-from-data-source",
        }
        for item in cases
    )
    assert all(
        item["gold_vault_paths"] if item["expected_behavior"] == "answer" else not item["gold_vault_paths"]
        for item in cases
    )
    # 晋升门槛（机械校验）：approved 条目引用的原文必须存在于同步语料中，
    # 防止评测 Recall 恒为 0 的无效条目混入 Golden。
    knowledge_root = Path(__file__).resolve().parents[1] / "knowledge"
    unreachable = [
        (item["case_id"], path)
        for item in cases
        if item["expected_behavior"] == "answer"
        for path in item["gold_vault_paths"]
        if not (knowledge_root / path).is_file()
    ]
    assert not unreachable, f"golden entries reference missing vault files: {unreachable}"


def test_repository_v2_golden_records_conform_to_published_schema():
    import jsonschema

    schema_path = Path(__file__).resolve().parents[1] / "evaluation" / "datasets" / "mindgraph_golden_v2.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    for item in load_golden_dataset():
        jsonschema.validate(item, schema)


def test_candidate_dataset_contract_requires_pending_review_and_is_separate_from_golden():
    candidate_cases = [candidate_case("C-1"), candidate_case("C-2", behavior="abstain", paths=[])]
    validated = validate_candidate_cases(candidate_cases)
    assert validated == candidate_cases

    with pytest.raises(ValueError, match=r"must remain pending"):
        bad = candidate_case("C-3")
        bad["validation_status"] = "approved"
        validate_candidate_cases([bad])

    with pytest.raises(ValueError, match=r"must use source='generated_candidate'"):
        bad = candidate_case("C-4")
        bad["source"] = "human-authored"
        validate_candidate_cases([bad])

    with pytest.raises(ValueError, match=r"require expected_relations"):
        bad = candidate_case("C-5")
        bad["graph_needed"] = True
        validate_candidate_cases([bad])

    with pytest.raises(ValueError, match=r"missing required field.*validation_status"):
        bad = candidate_case("C-6")
        bad.pop("validation_status")
        validate_candidate_cases([bad])


def test_candidate_dataset_path_is_separate_from_golden():
    assert DEFAULT_CANDIDATE_DATASET_PATH.name == "mindgraph_candidates_v2.jsonl"
    assert DEFAULT_CANDIDATE_DATASET_PATH != Path(
        __file__).resolve().parent.parent / "evaluation" / "datasets" / "mindgraph_golden.jsonl"


def test_dataset_sha256_is_deterministic_and_stable(tmp_path: Path):
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps(case(), ensure_ascii=False) + "\n", encoding="utf-8")
    h1 = dataset_sha256(path)
    h2 = dataset_sha256(path)
    assert h1 == h2
    assert len(h1) == 64


def test_retrieval_report_carries_dataset_snapshot_identity():
    retrieval_trace = trace([], [], [], [], ["gold.md"])
    retrieval_trace.latency_ms = {"total_retrieval_ms": 12.5}
    report = evaluate_retrieval_cases([case("snapshot")], lambda _case: retrieval_trace, dataset_digest="a" * 64)

    assert report["dataset_sha256"] == "a" * 64
    assert report["sample_size"] == 1
    assert report["summary"]["p50_retrieval_ms"] == 12.5
    assert report["summary"]["p95_retrieval_ms"] == 12.5


def test_load_candidate_dataset_from_path(tmp_path: Path):
    path = tmp_path / "candidates.jsonl"
    path.write_text(json.dumps(candidate_case("C-1"), ensure_ascii=False) + "\n", encoding="utf-8")
    loaded = load_candidate_dataset(path)
    assert loaded[0]["case_id"] == "C-1"
    assert loaded[0]["source"] == "generated_candidate"
    assert loaded[0]["validation_status"] == "pending"


def test_candidate_with_graph_needed_requires_expected_relations(tmp_path: Path):
    bad = candidate_case("C-7")
    bad["graph_needed"] = True
    bad["expected_relations"] = []
    with pytest.raises(ValueError, match=r"require expected_relations"):
        validate_candidate_cases([bad])

    good = candidate_case("C-8")
    good["graph_needed"] = True
    good["expected_relations"] = [
        {"source_path": "policies/expense-general-v2.md", "target_path": "workflows/no-invoice-exception.md", "relation_type": "REQUIRES"}
    ]
    validated = validate_candidate_cases([good])
    assert validated == [good]


def test_candidate_without_query_type_is_rejected(tmp_path: Path):
    bad = candidate_case("C-9")
    bad.pop("query_type")
    with pytest.raises(ValueError, match=r"missing required field.*query_type"):
        validate_candidate_cases([bad])


def test_metrics_and_stage_attribution():
    cases = [
        case("final", paths=["a.md", "b.md"]),
        case("retrieved", paths=["a.md"]),
        case("ranked", paths=["a.md"]),
        case("missing", paths=["a.md"]),
    ]
    traces = {
        "final": trace(["a.md"], [], ["a.md", "x.md"], ["x.md", "a.md"], ["x.md", "a.md"]),
        "retrieved": trace(["a.md"], [], [], [], []),
        "ranked": trace([], [], ["a.md"], [], []),
        "missing": trace(["x.md"], [], ["x.md"], ["x.md"], ["x.md"]),
    }
    result = evaluate_retrieval_cases(cases, lambda c: traces[c["case_id"]], top_k=2)
    rows = {row["case_id"]: row for row in result["details"]}
    assert rows["final"]["metrics"] == {"recall_at_k": 0.5, "precision_at_k": 0.5, "mrr": 0.5, "ndcg_at_k": 0.3869}
    assert rows["final"]["evidence_stages"] == {"a.md": "final", "b.md": "not_retrieved"}
    assert rows["final"]["failure_stage"] == "not_retrieved"
    assert rows["retrieved"]["evidence_stages"] == {"a.md": "retrieved_not_ranked"}
    assert rows["retrieved"]["failure_stage"] == "retrieved_not_ranked"
    assert rows["ranked"]["evidence_stages"] == {"a.md": "ranked_not_final"}
    assert rows["ranked"]["failure_stage"] == "ranked_not_final"
    assert rows["missing"]["evidence_stages"] == {"a.md": "not_retrieved"}
    assert rows["missing"]["failure_stage"] == "not_retrieved"
    assert result["summary"]["recall_at_k"] == 0.125
    assert result["summary"]["precision_at_k"] == 0.125
    assert result["summary"]["mrr"] == 0.125


def test_failure_stage_uses_earliest_loss_among_missing_evidence():
    value = case(paths=["final.md", "ranked.md", "missing.md"])
    result = evaluate_retrieval_cases(
        [value],
        lambda _case: trace(
            ["final.md", "ranked.md"],
            [],
            ["final.md", "ranked.md"],
            ["final.md", "ranked.md"],
            ["final.md"],
        ),
        top_k=3,
    )

    detail = result["details"][0]
    assert detail["evidence_stages"] == {
        "final.md": "final",
        "ranked.md": "ranked_not_final",
        "missing.md": "not_retrieved",
    }
    assert detail["failure_stage"] == "not_retrieved"
    assert result["failed_cases"][0]["failure_stage"] == "not_retrieved"


def test_precision_uses_k_for_short_results_and_mrr_uses_full_ranking():
    cases = [case("short", paths=["gold.md"]), case("beyond-k", paths=["gold.md"])]
    traces = {
        "short": trace([], [], [], [], ["gold.md"]),
        "beyond-k": trace([], [], [], [], ["x.md", "y.md", "gold.md"]),
    }

    result = evaluate_retrieval_cases(cases, lambda value: traces[value["case_id"]], top_k=2)
    rows = {row["case_id"]: row for row in result["details"]}

    assert rows["short"]["metrics"] == {"recall_at_k": 1.0, "precision_at_k": 0.5, "mrr": 1.0, "ndcg_at_k": 1.0}
    assert rows["beyond-k"]["metrics"] == {"recall_at_k": 0.0, "precision_at_k": 0.0, "mrr": 0.3333, "ndcg_at_k": 0.0}
    assert rows["beyond-k"]["evidence_stages"] == {"gold.md": "final"}
    assert rows["beyond-k"]["failure_stage"] == "ranked_not_final"


def test_retrieval_report_records_observed_graph_activation():
    graph_case = case("graph-case", paths=["linked.md"])
    graph_case["graph_needed"] = True
    graph_case["expected_relations"] = [
        {"source_path": "root.md", "target_path": "linked.md", "relation_type": "related_to"}
    ]
    retrieval_trace = trace([], [], [], [], ["root.md", "linked.md"])
    retrieval_trace.graph_enabled = True
    retrieval_trace.graph_hops = 1
    retrieval_trace.graph_links = [{"relation_id": "rel-1"}]
    retrieval_trace.candidate_counts = {"final": 2, "graph_expanded": 1}

    result = evaluate_retrieval_cases([graph_case], lambda _case: retrieval_trace, top_k=2)

    assert result["details"][0]["graph"] == {
        "enabled": True,
        "hops": 1,
        "expanded_candidates": 1,
        "relation_ids": ["rel-1"],
    }
    assert result["graph_diagnostics"] == {
        "enabled_cases": 1,
        "activated_cases": 1,
        "expanded_candidates": 1,
        "activation_rate": 1.0,
        "comparable_for_graph_gain": True,
        "limitations": [],
    }


def test_graph_enabled_report_without_expansion_is_not_comparable_for_gain():
    cases = [case("no-expansion", paths=["root.md"])]
    retrieval_trace = trace([], [], [], [], ["root.md"])
    retrieval_trace.graph_enabled = True
    retrieval_trace.candidate_counts = {"final": 1, "graph_expanded": 0}

    result = evaluate_retrieval_cases(cases, lambda _case: retrieval_trace, top_k=1)

    assert result["graph_diagnostics"]["enabled_cases"] == 1
    assert result["graph_diagnostics"]["activated_cases"] == 0
    assert result["graph_diagnostics"]["comparable_for_graph_gain"] is False
    assert result["graph_diagnostics"]["limitations"] == ["graph_enabled_but_no_expansion_observed"]


def test_abstain_is_visible_but_not_scored_and_questions_are_opt_in():
    cases = [case("answer", question="secret", paths=["a.md"]), case("refuse", "abstain", [], "private")]
    result = evaluate_retrieval_cases(cases, lambda c: trace([], [], [], [], []))
    json.dumps(result, ensure_ascii=False)
    assert result["counts"] == {"answer": 1, "abstain": 1}
    assert result["details"][1]["scored"] is False
    assert "reason" in result["details"][1]
    assert all("question" not in row for row in result["details"])
    assert all("question" not in row for row in result["failed_cases"])
    with_questions = evaluate_retrieval_cases(cases, lambda c: trace([], [], [], [], []), include_questions=True)
    json.dumps(with_questions, ensure_ascii=False)
    assert with_questions["details"][0]["question"] == "secret"
    assert with_questions["failed_cases"][0]["question"] == "secret"


def test_retrieve_failure_identifies_case_and_preserves_cause():
    cause = RuntimeError("backend unavailable")
    def retrieve(_case):
        raise cause
    with pytest.raises(RuntimeError, match=r"case_id 'A'.*retrieval failed") as error:
        evaluate_retrieval_cases([case()], retrieve)
    assert error.value.__cause__ is cause
