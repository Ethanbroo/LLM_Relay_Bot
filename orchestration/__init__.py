"""Phase 6: Multi-LLM Orchestration

This module implements:
- Model registry (closed world)
- Prompt construction (deterministic)
- Response parsing (strict)
- Similarity scoring (SBERT-based)
- Consensus algorithm (numeric)
- Escalation logic (hard rules)
- Orchestration decision (auditable)

Phase 6 Invariants:
1. LLMs are stateless and replaceable
2. All model outputs are treated as untrusted data
3. Consensus is numeric and deterministic
4. No self-reflection, self-revision, or recursive prompting
5. No model can see another model's output
6. No model can influence routing logic
7. Final decisions are made by code, not text
8. Every step is auditable and replayable
9. Phase 6 never emits executable instructions
10. Phase 6 output must re-enter Phase 1 validation
"""

__version__ = "1.0.0"
