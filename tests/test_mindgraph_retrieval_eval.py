import json
from pathlib import Path

import pytest

from evaluation.mindgraph_retrieval_eval import (
    evaluate_retrieval_cases,
    load_golden_dataset,
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

    assert len(cases) == 12
    assert {item["dataset_version"] for item in cases} == {"2.1.0"}
    assert {item["expected_behavior"] for item in cases} == {"answer", "abstain"}
    assert {item["split"] for item in cases} == {"development", "regression"}
    assert {item["category"] for item in cases} >= {
        "version",
        "supersession",
        "exception",
        "cross_policy",
        "no_answer",
        "ambiguity",
    }
    assert all(item["label_source"] == "human-authored-from-demo-vault" for item in cases)
    assert all(
        item["gold_vault_paths"] if item["expected_behavior"] == "answer" else not item["gold_vault_paths"]
        for item in cases
    )


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
    assert rows["final"]["metrics"] == {"recall_at_k": 0.5, "precision_at_k": 0.5, "mrr": 0.5}
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

    assert rows["short"]["metrics"] == {"recall_at_k": 1.0, "precision_at_k": 0.5, "mrr": 1.0}
    assert rows["beyond-k"]["metrics"] == {"recall_at_k": 0.0, "precision_at_k": 0.0, "mrr": 0.3333}
    assert rows["beyond-k"]["evidence_stages"] == {"gold.md": "final"}
    assert rows["beyond-k"]["failure_stage"] == "ranked_not_final"


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
