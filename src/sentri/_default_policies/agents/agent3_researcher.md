---
type: agent_config
name: researcher
version: 1
status: stub
---

# Agent 3: The Researcher (Documentation Search)

> **Status**: STUB for POC -- This agent is a pass-through in the current release.
> All remediation actions are hardcoded in the alert policy files (`alerts/*.md`).

## Purpose

Search Oracle My Oracle Support (MOS), internal runbooks, knowledge base articles,
and historical resolution data to find the best remediation strategy for a given
database issue. In the full implementation, the Researcher bridges the gap between
detecting a problem and knowing how to fix it.

For the POC, this agent does not perform any active research. Instead, the
remediation actions are defined statically in each alert policy file, and the
orchestrator reads them directly. The Researcher exists as a placeholder to
establish the interface contract and ensure a clean migration path when
intelligent research capabilities are added.

## POC Behavior

In the current POC implementation, the Researcher:

1. **Receives** an alert type and verification report from the orchestrator.
2. **Returns** the static remediation plan defined in `alerts/<alert_type>.md`
   without modification.
3. **Logs** the pass-through at INFO level for audit trail completeness.
4. **Does not** connect to any external services, APIs, or databases.

The orchestrator may bypass this agent entirely in the POC, reading the alert
policy files directly. Both paths produce identical results.

### Pass-Through Interface

```
Input:  alert_type (str), verification_report (dict)
Output: remediation_plan (dict) -- loaded directly from alerts/<alert_type>.md
```

## Future Scope

### Phase 1: MOS Document Search (Month 4-6)

- Integrate with Oracle MOS API to search for relevant documents
- Match alert signatures against known MOS bug database
- Extract recommended patches and workarounds
- Cache MOS results in SQLite `cache` table (TTL: 24 hours)

### Phase 2: RAG with Oracle Documentation (Month 6-9)

- Build vector embeddings of Oracle documentation corpus
- Index internal runbooks, wiki pages, and past resolution notes
- Implement semantic search over combined knowledge base
- Rank results by relevance to the specific alert context
- Store embeddings in local vector store (ChromaDB or similar)

### Phase 3: LLM-Powered Reasoning (Month 9-12)

- Use Vertex AI (Gemini) to reason about complex, multi-factor issues
- Generate novel remediation strategies for previously unseen problems
- Cross-reference multiple data sources (MOS, docs, past resolutions)
- Provide confidence-scored recommendations with citations
- Human-in-the-loop review for LLM-generated remediation plans

### Phase 4: Continuous Learning (Month 12+)

- Feed execution outcomes back into the research pipeline
- Build a resolution pattern library from successful fixes
- Identify recurring issues and suggest preventive measures
- Auto-generate runbook entries from successful novel resolutions
