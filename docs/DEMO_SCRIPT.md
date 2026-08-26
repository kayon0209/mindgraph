# MindGraph Evidence Demo Script

1. Ask: `2026年8月发生的费用最晚应在多少天内提交？`
   - Show the active V2 citation and effective date.
2. Ask: `客户晚餐和差旅餐补能同时报销吗？`
   - Show Hybrid retrieval, the controlled graph toggle, and the confirmed evidence relation when explicitly enabled.
3. Open the citation and inspect source path, section, chunk ID, version, and lifecycle metadata.
4. Review a proposed relation in Relation Review. Confirmed edges may be traversed; proposed edges may not.
5. Demonstrate a denied workspace request. Confirm that the title, excerpt, relation, and citation are not returned.
6. Demonstrate an old/new policy conflict. Confirm generation stops and both versions are surfaced for human resolution.
7. Open Evaluation and show dataset version, run status, recall, citation/refusal metrics, latency, and graph-gate decision.

All examples use synthetic demo data. Real enterprise data and provider credentials must not be committed to the repository.
