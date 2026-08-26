#!/usr/bin/env python3
"""Deterministic candidate generation from demo-vault for Phase 1.

Generates mindgraph_candidates_v2.jsonl with source=generated_candidate,
validation_status=pending. Does NOT modify golden datasets.

Usage:
    python scripts/generate_candidates.py
    python scripts/generate_candidates.py --check  # validate only
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))
DEMO_VAULT = ROOT / "demo-vault"
CANDIDATE_PATH = ROOT / "evaluation" / "datasets" / "mindgraph_candidates_v2.jsonl"
GOLDEN_PATH = ROOT / "evaluation" / "datasets" / "mindgraph_golden.jsonl"
GOLDEN_V2_PATH = ROOT / "evaluation" / "datasets" / "mindgraph_golden_v2.jsonl"

# Deterministic seed for reproducibility
_GENERATION_SEED = "mindgraph-phase1-candidates-v1"


def _hash_id(text: str, prefix: str) -> str:
    h = hashlib.sha256((_GENERATION_SEED + text).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{h}"


def _read_demo_vault() -> list[dict]:
    """Read all markdown files from demo-vault and extract metadata."""
    docs = []
    for md_file in DEMO_VAULT.rglob("*.md"):
        if md_file.name.startswith("case-"):
            category = "case"
        elif md_file.parent.name == "policies":
            category = "policy"
        elif md_file.parent.name == "workflows":
            category = "workflow"
        else:
            category = "other"
        
        content = md_file.read_text(encoding="utf-8")
        # Extract frontmatter
        frontmatter = {}
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                try:
                    import yaml
                    frontmatter = yaml.safe_load(content[:end]) or {}
                except ImportError:
                    # Fallback: simple parsing
                    for line in content[3:end].strip().split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            frontmatter[k.strip()] = v.strip().strip('"').strip("'")
        
        rel_path = str(md_file.relative_to(DEMO_VAULT))
        docs.append({
            "path": rel_path,
            "title": frontmatter.get("title", "") or md_file.stem.replace("-", " "),
            "policy_key": frontmatter.get("policy_key", ""),
            "status": frontmatter.get("status", ""),
            "version": frontmatter.get("version", ""),
            "effective_from": frontmatter.get("effective_from", ""),
            "effective_to": frontmatter.get("effective_to", ""),
            "owner": frontmatter.get("owner", ""),
            "category": category,
            "content": content,
        })
    return docs


def _generate_candidates(docs: list[dict]) -> list[dict]:
    """Generate candidate cases from demo-vault documents."""
    candidates = []
    
    # Policy documents
    policies = [d for d in docs if d["category"] == "policy"]
    workflows = [d for d in docs if d["category"] == "workflow"]
    cases = [d for d in docs if d["category"] == "case"]
    
    # === 精确事实 (exact_fact) - 10 candidates ===
    exact_facts = [
        {
            "question": "费用报销管理办法V2中，单笔含税金额超过多少需要成本中心负责人审批？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["5000元"],
            "forbidden_facts": ["10000元", "20000元"],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "国内差旅标准V3中，一线城市住宿上限是多少？",
            "gold_paths": ["policies/travel-domestic-v3.md"],
            "required_facts": ["800元"],
            "forbidden_facts": ["600元"],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "差旅餐补标准V2中，国内差旅餐补为每人每天多少元？",
            "gold_paths": ["policies/travel-meal-v2.md"],
            "required_facts": ["180元"],
            "forbidden_facts": [],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "客户招待费用标准V2中，人均含税金额不超过多少？",
            "gold_paths": ["policies/client-entertainment-v2.md"],
            "required_facts": ["400元"],
            "forbidden_facts": [],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "远程办公设备补贴中，正式员工每两个自然年可申请不超过多少元？",
            "gold_paths": ["policies/remote-work-equipment-v1.md"],
            "required_facts": ["2000元"],
            "forbidden_facts": [],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "费用报销管理办法V2中，员工应在费用发生后多少个自然日内提交报销？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["30个自然日"],
            "forbidden_facts": ["60个自然日"],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "差旅餐补标准V2中，客户已提供工作餐应从当日餐补中扣除多少元？",
            "gold_paths": ["policies/travel-meal-v2.md"],
            "required_facts": ["60元"],
            "forbidden_facts": [],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "客户招待费用标准V2中，单次总额超过多少元还需销售运营负责人批准？",
            "gold_paths": ["policies/client-entertainment-v2.md"],
            "required_facts": ["5000元"],
            "forbidden_facts": [],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "无票例外审批流程中，单笔超过多少元的无票费用还需财务负责人批准？",
            "gold_paths": ["workflows/no-invoice-exception.md"],
            "required_facts": ["1000元"],
            "forbidden_facts": [],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "差旅例外审批中，紧急客户事件无法事前审批的应在行程结束后多少个工作日内补充审批？",
            "gold_paths": ["workflows/travel-exception.md"],
            "required_facts": ["3个工作日"],
            "forbidden_facts": [],
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
    ]
    
    for i, cf in enumerate(exact_facts, 1):
        candidates.append({
            "case_id": _hash_id(cf["question"], f"cand-exact-fact-{i}"),
            "question": cf["question"],
            "category": "exact_fact",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        })
    
    # === 多条件组合 (multi_condition) - 8 candidates ===
    multi_conditions = [
        {
            "question": "单笔含税22000元的普通费用在2026年8月需要哪些审批？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["直属主管", "成本中心负责人", "财务负责人复核"],
            "forbidden_facts": [],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "一线城市出差5天，住宿和餐补分别可以报销多少？",
            "gold_paths": ["policies/travel-domestic-v3.md", "policies/travel-meal-v2.md"],
            "required_facts": ["住宿800元/晚", "餐补180元/天"],
            "forbidden_facts": [],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": False,
        },
        {
            "question": "客户晚餐1200元且当天差旅餐补应如何处理？",
            "gold_paths": ["policies/travel-meal-v2.md", "policies/client-entertainment-v2.md"],
            "required_facts": ["扣除60元", "不能重复申领"],
            "forbidden_facts": ["仍领取完整180元"],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": False,
        },
        {
            "question": "远程办公购买1200元显示器需要走什么流程？",
            "gold_paths": ["policies/remote-work-equipment-v1.md", "workflows/lightweight-procurement.md"],
            "required_facts": ["两个供应商报价", "直属主管记录选择理由"],
            "forbidden_facts": [],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": False,
        },
        {
            "question": "单笔1500元无法取得发票时需要哪些材料和审批？",
            "gold_paths": ["policies/invoice-compliance-v1.md", "workflows/no-invoice-exception.md"],
            "required_facts": ["费用发生证明", "支付凭证", "无法取得票据的原因", "直属主管", "税务专员"],
            "forbidden_facts": ["手写说明即可"],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": False,
        },
        {
            "question": "超出国内差旅标准时需要提供什么材料？",
            "gold_paths": ["workflows/travel-exception.md", "policies/travel-domestic-v3.md"],
            "required_facts": ["城市", "日期", "超标金额", "不可替代原因", "至少两个可比选项"],
            "forbidden_facts": [],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": False,
        },
        {
            "question": "客户招待费用需要满足哪些条件？",
            "gold_paths": ["policies/client-entertainment-v2.md"],
            "required_facts": ["直属主管批准", "记录客户单位", "记录参与人数", "记录业务目的", "人均不超过400元"],
            "forbidden_facts": [],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "高铁行程在5小时以内和超过5小时分别选择什么座位？",
            "gold_paths": ["policies/travel-domestic-v3.md"],
            "required_facts": ["5小时以内选择二等座", "超过5小时可选择一等座", "需直属主管说明业务必要性"],
            "forbidden_facts": [],
            "query_type": "multi_condition",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
    ]
    
    for i, cf in enumerate(multi_conditions, 1):
        candidates.append({
            "case_id": _hash_id(cf["question"], f"cand-multi-condition-{i}"),
            "question": cf["question"],
            "category": "multi_condition",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        })
    
    # === 例外条款 (exception) - 8 candidates ===
    exceptions = [
        {
            "question": "无发票费用有什么例外流程？",
            "gold_paths": ["workflows/no-invoice-exception.md"],
            "required_facts": ["费用发生证明", "支付凭证", "无法取得票据的原因", "供应商信息", "直属主管确认", "税务专员判断"],
            "forbidden_facts": [],
            "query_type": "exception",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "workflows/no-invoice-exception.md", "target_path": "policies/invoice-compliance-v1.md", "relation_type": "REQUIRES"}
            ],
        },
        {
            "question": "新旧费用报销管理办法冲突时应适用哪个版本？",
            "gold_paths": ["policies/expense-general-v2.md", "policies/expense-general-v1.md"],
            "required_facts": ["V1已废止", "V2替代V1", "按最新active版本"],
            "forbidden_facts": [],
            "query_type": "exception",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/expense-general-v2.md", "target_path": "policies/expense-general-v1.md", "relation_type": "SUPERSEDES"}
            ],
        },
        {
            "question": "制度条款互相矛盾时应该如何处理？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["以生效日期最新且状态为active的版本为准"],
            "forbidden_facts": [],
            "query_type": "exception",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": False,
        },
        {
            "question": "出差当日往返且总时长不足8小时有餐补吗？",
            "gold_paths": ["policies/travel-meal-v2.md"],
            "required_facts": ["不发放餐补"],
            "forbidden_facts": ["发放50%餐补"],
            "query_type": "exception",
            "difficulty": "easy",
            "expected_route": "structured_fallback",
            "graph_needed": False,
        },
        {
            "question": "出发前24小时内因个人原因改签产生的费用可以报销吗？",
            "gold_paths": ["policies/travel-domestic-v3.md"],
            "required_facts": ["不予报销"],
            "forbidden_facts": [],
            "query_type": "exception",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "因客户会议变更导致的改签应走什么流程？",
            "gold_paths": ["policies/travel-domestic-v3.md", "workflows/travel-exception.md"],
            "required_facts": ["进入差旅例外审批", "附客户通知"],
            "forbidden_facts": [],
            "query_type": "exception",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": False,
        },
        {
            "question": "政府及公共机构相关人员的招待必须经过什么程序？",
            "gold_paths": ["policies/client-entertainment-v2.md"],
            "required_facts": ["先经过合规审查"],
            "forbidden_facts": [],
            "query_type": "exception",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "同一申请人一个季度累计无票费用不得超过多少？",
            "gold_paths": ["workflows/no-invoice-exception.md"],
            "required_facts": ["不得超过3000元"],
            "forbidden_facts": [],
            "query_type": "exception",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
    ]
    
    for i, cf in enumerate(exceptions, 1):
        entry = {
            "case_id": _hash_id(cf["question"], f"cand-exception-{i}"),
            "question": cf["question"],
            "category": "exception",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        }
        if cf.get("expected_relations"):
            entry["expected_relations"] = cf["expected_relations"]
        candidates.append(entry)
    
    # === 生效时间/版本 (versioned) - 8 candidates ===
    versioned = [
        {
            "question": "费用报销管理办法V2的生效日期是何时？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["2026-07-01"],
            "forbidden_facts": [],
            "query_type": "versioned_policy",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "费用报销管理办法V1的废止日期是何时？",
            "gold_paths": ["policies/expense-general-v1.md"],
            "required_facts": ["2026-06-30"],
            "forbidden_facts": [],
            "query_type": "versioned_policy",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "国内差旅标准V3的生效日期是何时？",
            "gold_paths": ["policies/travel-domestic-v3.md"],
            "required_facts": ["2026-07-01"],
            "forbidden_facts": [],
            "query_type": "versioned_policy",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "差旅餐补标准V2的生效日期是何时？",
            "gold_paths": ["policies/travel-meal-v2.md"],
            "required_facts": ["2026-07-01"],
            "forbidden_facts": [],
            "query_type": "versioned_policy",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "客户招待费用标准V2的生效日期是何时？",
            "gold_paths": ["policies/client-entertainment-v2.md"],
            "required_facts": ["2026-05-01"],
            "forbidden_facts": [],
            "query_type": "versioned_policy",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "2026年8月发生的费用应适用哪个版本的费用报销管理办法？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["V2", "2026-07-01生效"],
            "forbidden_facts": ["V1"],
            "query_type": "versioned_policy",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "远程办公设备补贴的生效日期是何时？",
            "gold_paths": ["policies/remote-work-equipment-v1.md"],
            "required_facts": ["2026-03-01"],
            "forbidden_facts": [],
            "query_type": "versioned_policy",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "票据与税务合规要求的生效日期是何时？",
            "gold_paths": ["policies/invoice-compliance-v1.md"],
            "required_facts": ["2026-01-01"],
            "forbidden_facts": [],
            "query_type": "versioned_policy",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
    ]
    
    for i, cf in enumerate(versioned, 1):
        candidates.append({
            "case_id": _hash_id(cf["question"], f"cand-versioned-{i}"),
            "question": cf["question"],
            "category": "versioned_policy",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        })
    
    # === 多文档冲突 (conflict) - 6 candidates ===
    conflicts = [
        {
            "question": "客户晚餐和差旅餐补能同时报销吗？",
            "gold_paths": ["policies/travel-meal-v2.md", "policies/client-entertainment-v2.md"],
            "required_facts": ["不能重复申领", "对应餐次扣减60元"],
            "forbidden_facts": ["可以同时报销"],
            "query_type": "conflict",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/travel-meal-v2.md", "target_path": "policies/client-entertainment-v2.md", "relation_type": "CONFLICTS_WITH"}
            ],
        },
        {
            "question": "请分别说明住宿和交通的审批要求。",
            "gold_paths": ["policies/travel-domestic-v3.md"],
            "required_facts": ["高铁5小时以内原则上选择二等座", "超过5小时可选择一等座需直属主管说明业务必要性"],
            "forbidden_facts": [],
            "query_type": "conflict",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": False,
        },
        {
            "question": "费用报销管理办法和无票例外审批流程有什么关系？",
            "gold_paths": ["policies/expense-general-v2.md", "workflows/no-invoice-exception.md"],
            "required_facts": ["无票时应执行无票例外审批流程"],
            "forbidden_facts": [],
            "query_type": "conflict",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/expense-general-v2.md", "target_path": "workflows/no-invoice-exception.md", "relation_type": "REQUIRES"}
            ],
        },
        {
            "question": "远程办公设备补贴和小额采购审批有什么关系？",
            "gold_paths": ["policies/remote-work-equipment-v1.md", "workflows/lightweight-procurement.md"],
            "required_facts": ["单件超过1000元的设备应先走小额采购审批"],
            "forbidden_facts": [],
            "query_type": "conflict",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/remote-work-equipment-v1.md", "target_path": "workflows/lightweight-procurement.md", "relation_type": "REQUIRES"}
            ],
        },
        {
            "question": "差旅例外审批和国内差旅标准有什么关系？",
            "gold_paths": ["workflows/travel-exception.md", "policies/travel-domestic-v3.md"],
            "required_facts": ["超出国内差旅标准时应走差旅例外审批"],
            "forbidden_facts": [],
            "query_type": "conflict",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "workflows/travel-exception.md", "target_path": "policies/travel-domestic-v3.md", "relation_type": "APPLIES_TO"}
            ],
        },
        {
            "question": "新旧费用报销管理办法有什么区别？",
            "gold_paths": ["policies/expense-general-v2.md", "policies/expense-general-v1.md"],
            "required_facts": ["V1要求60天", "V2要求30天", "V2替代V1"],
            "forbidden_facts": [],
            "query_type": "conflict",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/expense-general-v2.md", "target_path": "policies/expense-general-v1.md", "relation_type": "SUPERSEDES"}
            ],
        },
    ]
    
    for i, cf in enumerate(conflicts, 1):
        entry = {
            "case_id": _hash_id(cf["question"], f"cand-conflict-{i}"),
            "question": cf["question"],
            "category": "conflict",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        }
        if cf.get("expected_relations"):
            entry["expected_relations"] = cf["expected_relations"]
        candidates.append(entry)
    
    # === 无答案/需澄清 (no_answer) - 8 candidates ===
    no_answers = [
        {
            "question": "公司是否报销宠物医疗费用？",
            "gold_paths": [],
            "required_facts": ["演示制度无相关依据"],
            "forbidden_facts": ["编造报销比例或上限"],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "我想买一台设备，能不能报销？",
            "gold_paths": [],
            "required_facts": ["需要澄清设备类型、金额、用途和申请人资格"],
            "forbidden_facts": ["直接承诺可以报销"],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "我能报销婚礼费用吗？",
            "gold_paths": [],
            "required_facts": ["演示制度无相关依据"],
            "forbidden_facts": [],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "公司的年终奖金政策是什么？",
            "gold_paths": [],
            "required_facts": ["演示制度无相关依据"],
            "forbidden_facts": [],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "我能报销旅游费用吗？",
            "gold_paths": [],
            "required_facts": ["演示制度无相关依据"],
            "forbidden_facts": [],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "公司的股票期权政策是什么？",
            "gold_paths": [],
            "required_facts": ["演示制度无相关依据"],
            "forbidden_facts": [],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "我能报销健身房会员费用吗？",
            "gold_paths": [],
            "required_facts": ["演示制度无相关依据"],
            "forbidden_facts": [],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "公司的带薪休假政策是什么？",
            "gold_paths": [],
            "required_facts": ["演示制度无相关依据"],
            "forbidden_facts": [],
            "query_type": "no_answer",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
    ]
    
    for i, cf in enumerate(no_answers, 1):
        candidates.append({
            "case_id": _hash_id(cf["question"], f"cand-no-answer-{i}"),
            "question": cf["question"],
            "category": "no_answer",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "abstain",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        })
    
    # === 权限受限 (acl_restricted) - 6 candidates ===
    acl_cases = [
        {
            "question": "我能查看财务负责人的审批记录吗？",
            "gold_paths": [],
            "required_facts": ["需要相应权限"],
            "forbidden_facts": [],
            "query_type": "acl_restricted",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
            "acl_context": {"roles": ["employee"], "denied_resources": ["finance_approvals"]},
        },
        {
            "question": "请列出所有员工的报销记录。",
            "gold_paths": [],
            "required_facts": ["需要管理员权限"],
            "forbidden_facts": [],
            "query_type": "acl_restricted",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
            "acl_context": {"roles": ["employee"], "denied_resources": ["all_employee_records"]},
        },
        {
            "question": "我能修改其他部门的制度文档吗？",
            "gold_paths": [],
            "required_facts": ["需要制度责任人权限"],
            "forbidden_facts": [],
            "query_type": "acl_restricted",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
            "acl_context": {"roles": ["employee"], "denied_actions": ["modify_other_dept_policy"]},
        },
        {
            "question": "请删除报销审批流程。",
            "gold_paths": [],
            "required_facts": ["需要管理员权限"],
            "forbidden_facts": [],
            "query_type": "acl_restricted",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
            "acl_context": {"roles": ["employee"], "denied_actions": ["delete_workflow"]},
        },
        {
            "question": "我能查看其他部门的预算信息吗？",
            "gold_paths": [],
            "required_facts": ["需要跨部门授权"],
            "forbidden_facts": [],
            "query_type": "acl_restricted",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
            "acl_context": {"roles": ["employee"], "denied_resources": ["other_dept_budget"]},
        },
        {
            "question": "请导出所有员工的薪资数据。",
            "gold_paths": [],
            "required_facts": ["需要HR管理员权限"],
            "forbidden_facts": [],
            "query_type": "acl_restricted",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
            "acl_context": {"roles": ["employee"], "denied_resources": ["salary_data"]},
        },
    ]
    
    for i, cf in enumerate(acl_cases, 1):
        entry = {
            "case_id": _hash_id(cf["question"], f"cand-acl-{i}"),
            "question": cf["question"],
            "category": "acl_restricted",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "abstain",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": cf["acl_context"],
            "notes": "Phase 1 deterministic candidate from demo-vault",
        }
        candidates.append(entry)
    
    # === 同义表达和缩写 (synonym_abbrev) - 6 candidates ===
    synonyms = [
        {
            "question": "报销单笔超过5000元需要谁审批？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["直属主管", "成本中心负责人"],
            "forbidden_facts": [],
            "query_type": "synonym_abbrev",
            "difficulty": "easy",
            "expected_route": "structured_fallback",
            "graph_needed": False,
        },
        {
            "question": "出差住宿标准中，一线城市的上限是多少？",
            "gold_paths": ["policies/travel-domestic-v3.md"],
            "required_facts": ["800元"],
            "forbidden_facts": [],
            "query_type": "synonym_abbrev",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "差旅的餐补标准是多少？",
            "gold_paths": ["policies/travel-meal-v2.md"],
            "required_facts": ["180元/天"],
            "forbidden_facts": [],
            "query_type": "synonym_abbrev",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "客户招待的费用上限是多少？",
            "gold_paths": ["policies/client-entertainment-v2.md"],
            "required_facts": ["人均400元"],
            "forbidden_facts": [],
            "query_type": "synonym_abbrev",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "远程办公设备的补贴周期是多久？",
            "gold_paths": ["policies/remote-work-equipment-v1.md"],
            "required_facts": ["每两个自然年"],
            "forbidden_facts": [],
            "query_type": "synonym_abbrev",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "无发票的费用如何处理？",
            "gold_paths": ["workflows/no-invoice-exception.md"],
            "required_facts": ["执行无票例外审批流程"],
            "forbidden_facts": [],
            "query_type": "synonym_abbrev",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
    ]
    
    for i, cf in enumerate(synonyms, 1):
        candidates.append({
            "case_id": _hash_id(cf["question"], f"cand-synonym-{i}"),
            "question": cf["question"],
            "category": "synonym_abbrev",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        })
    
    # === 需要关系扩展 (graph_needed) - 8 candidates ===
    graph_cases = [
        {
            "question": "费用报销管理办法V2中提到的无票例外流程具体是什么？",
            "gold_paths": ["policies/expense-general-v2.md", "workflows/no-invoice-exception.md"],
            "required_facts": ["缺少票据时应执行无票例外审批流程"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/expense-general-v2.md", "target_path": "workflows/no-invoice-exception.md", "relation_type": "REQUIRES"}
            ],
        },
        {
            "question": "国内差旅标准V3中提到的差旅例外审批适用于什么情况？",
            "gold_paths": ["policies/travel-domestic-v3.md", "workflows/travel-exception.md"],
            "required_facts": ["超出国内差旅标准时应走差旅例外审批"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/travel-domestic-v3.md", "target_path": "workflows/travel-exception.md", "relation_type": "APPLIES_TO"}
            ],
        },
        {
            "question": "差旅餐补标准V2中提到的客户招待费用标准有什么限制？",
            "gold_paths": ["policies/travel-meal-v2.md", "policies/client-entertainment-v2.md"],
            "required_facts": ["招待费不得与差旅餐补重复申领"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/travel-meal-v2.md", "target_path": "policies/client-entertainment-v2.md", "relation_type": "CONFLICTS_WITH"}
            ],
        },
        {
            "question": "远程办公设备补贴中提到的小额采购审批流程是什么？",
            "gold_paths": ["policies/remote-work-equipment-v1.md", "workflows/lightweight-procurement.md"],
            "required_facts": ["单件1000至5000元的办公设备应提供两个供应商报价"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/remote-work-equipment-v1.md", "target_path": "workflows/lightweight-procurement.md", "relation_type": "REQUIRES"}
            ],
        },
        {
            "question": "费用报销管理办法V2和费用报销管理办法V1有什么关系？",
            "gold_paths": ["policies/expense-general-v2.md", "policies/expense-general-v1.md"],
            "required_facts": ["V2替代V1"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/expense-general-v2.md", "target_path": "policies/expense-general-v1.md", "relation_type": "SUPERSEDES"}
            ],
        },
        {
            "question": "票据与税务合规要求中提到的无票例外审批流程是什么？",
            "gold_paths": ["policies/invoice-compliance-v1.md", "workflows/no-invoice-exception.md"],
            "required_facts": ["确因海外场景或不可抗力无法取得境内发票时应进入无票例外审批流程"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "exception_or_conflict",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/invoice-compliance-v1.md", "target_path": "workflows/no-invoice-exception.md", "relation_type": "REQUIRES"}
            ],
        },
        {
            "question": "客户招待费用标准V2和差旅餐补标准V2有什么冲突？",
            "gold_paths": ["policies/client-entertainment-v2.md", "policies/travel-meal-v2.md"],
            "required_facts": ["招待费不得与差旅餐补重复申领"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "policies/client-entertainment-v2.md", "target_path": "policies/travel-meal-v2.md", "relation_type": "CONFLICTS_WITH"}
            ],
        },
        {
            "question": "差旅例外审批和国内差旅标准V3有什么关系？",
            "gold_paths": ["workflows/travel-exception.md", "policies/travel-domestic-v3.md"],
            "required_facts": ["超出国内差旅标准时应走差旅例外审批"],
            "forbidden_facts": [],
            "query_type": "graph_needed",
            "difficulty": "medium",
            "expected_route": "cross_policy",
            "graph_needed": True,
            "expected_relations": [
                {"source_path": "workflows/travel-exception.md", "target_path": "policies/travel-domestic-v3.md", "relation_type": "APPLIES_TO"}
            ],
        },
    ]
    
    for i, cf in enumerate(graph_cases, 1):
        entry = {
            "case_id": _hash_id(cf["question"], f"cand-graph-{i}"),
            "question": cf["question"],
            "category": "graph_needed",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        }
        if cf.get("expected_relations"):
            entry["expected_relations"] = cf["expected_relations"]
        candidates.append(entry)
    
    # === Graph不应启用的对照问题 (graph_control) - 8 candidates ===
    graph_controls = [
        {
            "question": "费用报销管理办法V2的全文是什么？",
            "gold_paths": ["policies/expense-general-v2.md"],
            "required_facts": ["30个自然日", "5000元", "成本中心负责人", "财务负责人复核"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "请总结国内差旅标准V3的主要内容。",
            "gold_paths": ["policies/travel-domestic-v3.md"],
            "required_facts": ["一线城市住宿上限800元", "其他城市600元", "高铁5小时以内选择二等座"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "差旅餐补标准V2适用于哪些人？",
            "gold_paths": ["policies/travel-meal-v2.md"],
            "required_facts": ["国内差旅"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "客户招待费用标准V2的审批流程是什么？",
            "gold_paths": ["policies/client-entertainment-v2.md"],
            "required_facts": ["直属主管批准", "记录客户单位、参与人数和业务目的"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "远程办公设备补贴的适用范围是什么？",
            "gold_paths": ["policies/remote-work-equipment-v1.md"],
            "required_facts": ["显示器、键盘、鼠标和人体工学座椅"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "无票例外审批流程需要提交哪些材料？",
            "gold_paths": ["workflows/no-invoice-exception.md"],
            "required_facts": ["费用发生证明", "支付凭证", "无法取得票据的原因", "供应商信息"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "medium",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "小额采购审批的金额范围是什么？",
            "gold_paths": ["workflows/lightweight-procurement.md"],
            "required_facts": ["单件1000至5000元"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
        {
            "question": "差旅例外审批的时限要求是什么？",
            "gold_paths": ["workflows/travel-exception.md"],
            "required_facts": ["行程结束后3个工作日内"],
            "forbidden_facts": [],
            "query_type": "graph_control",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
        },
    ]
    
    for i, cf in enumerate(graph_controls, 1):
        candidates.append({
            "case_id": _hash_id(cf["question"], f"cand-graph-control-{i}"),
            "question": cf["question"],
            "category": "graph_control",
            "query_type": cf["query_type"],
            "split": "development",
            "expected_behavior": "answer",
            "gold_vault_paths": cf["gold_paths"],
            "required_facts": cf["required_facts"],
            "forbidden_facts": cf["forbidden_facts"],
            "dataset_version": "2.2.0",
            "label_source": "generated_candidate",
            "source": "generated_candidate",
            "validation_status": "pending",
            "expected_route": cf["expected_route"],
            "graph_needed": cf["graph_needed"],
            "difficulty": cf["difficulty"],
            "acl_context": {"roles": ["employee"]},
            "notes": "Phase 1 deterministic candidate from demo-vault",
        })
    
    return candidates


def _migrate_golden_to_v2() -> list[dict]:
    """Migrate existing golden cases to v2 schema with governance metadata."""
    golden = []
    for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        # Add governance metadata
        case["query_type"] = {
            "version": "versioned_policy",
            "supersession": "versioned_policy",
            "approval": "exact_fact",
            "limit": "exact_fact",
            "exception": "exception",
            "cross_policy": "multi_condition",
            "exception_workflow": "multi_condition",
            "case_reasoning": "multi_condition",
            "no_answer": "no_answer",
            "ambiguity": "no_answer",
        }.get(case["category"], "exact_fact")

        case["difficulty"] = {
            "version": "medium",
            "supersession": "medium",
            "approval": "medium",
            "limit": "easy",
            "exception": "medium",
            "cross_policy": "medium",
            "exception_workflow": "medium",
            "case_reasoning": "hard",
            "no_answer": "easy",
            "ambiguity": "easy",
        }.get(case["category"], "medium")

        case["expected_route"] = {
            "version": "factual",
            "supersession": "exception_or_conflict",
            "approval": "factual",
            "limit": "factual",
            "exception": "exception_or_conflict",
            "cross_policy": "cross_policy",
            "exception_workflow": "exception_or_conflict",
            "case_reasoning": "case_reasoning",
            "no_answer": "factual",
            "ambiguity": "factual",
        }.get(case["category"], "factual")

        case["graph_needed"] = case["category"] in ("exception", "cross_policy", "case_reasoning")
        if case["category"] in ("no_answer", "ambiguity"):
            case["graph_needed"] = False
        case["validation_status"] = "approved"
        case["source"] = case["label_source"]
        case["acl_context"] = {"roles": ["employee"]}
        case["notes"] = "Migrated from mindgraph_golden.jsonl to v2 schema"
        case["dataset_version"] = "2.2.0"
        golden.append(case)
    return golden


def main():
    parser = argparse.ArgumentParser(description="Generate Phase 1 candidate dataset")
    parser.add_argument("--check", action="store_true", help="Validate only, do not write")
    parser.add_argument("--golden-v2", action="store_true", help="Also generate golden_v2.jsonl")
    args = parser.parse_args()

    # Generate candidates
    docs = _read_demo_vault()
    candidates = _generate_candidates(docs)
    
    print(f"Generated {len(candidates)} candidate cases")
    
    # Validate candidates
    from evaluation.mindgraph_retrieval_eval import validate_candidate_cases
    try:
        validate_candidate_cases(candidates)
        print("Candidate validation: PASSED")
    except ValueError as e:
        print(f"Candidate validation: FAILED - {e}")
        return 1
    
    if not args.check:
        # Write candidates
        CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CANDIDATE_PATH.open("w", encoding="utf-8") as f:
            for case in candidates:
                f.write(json.dumps(case, ensure_ascii=False) + "\n")
        print(f"Written to {CANDIDATE_PATH}")
    
    if args.golden_v2:
        golden = _migrate_golden_to_v2()
        from evaluation.mindgraph_retrieval_eval import validate_golden_cases
        try:
            validate_golden_cases(golden)
            print("Golden v2 validation: PASSED")
        except ValueError as e:
            print(f"Golden v2 validation: FAILED - {e}")
            return 1
        
        if not args.check:
            with GOLDEN_V2_PATH.open("w", encoding="utf-8") as f:
                for case in golden:
                    f.write(json.dumps(case, ensure_ascii=False) + "\n")
            print(f"Written to {GOLDEN_V2_PATH}")
    
    # Print coverage summary
    query_types = {}
    for c in candidates:
        qt = c["query_type"]
        query_types[qt] = query_types.get(qt, 0) + 1
    print("\nCandidate coverage by query_type:")
    for qt, count in sorted(query_types.items()):
        print(f"  {qt}: {count}")
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
