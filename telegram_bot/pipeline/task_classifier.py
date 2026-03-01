"""Phase 3: Regex-based task classification. No LLM call."""

import re


def classify_task(anchor_text: str) -> str:
    """Classify the semantic anchor into a task type.

    Returns one of: RESEARCH, PLANNING, IMPLEMENTATION, HYBRID
    """
    text = anchor_text.lower()

    # Strong signals for RESEARCH only
    research_only = [
        r"\bresearch\b.*\bnot\b.*\bbuild\b",
        r"\bcompare\b.*\boptions\b",
        r"\banalyz[ei]\b.*\blandscape\b",
        r"\bsurvey\b.*\btools\b",
        r"\bfind\s+out\b",
        r"\bwhat\s+are\s+the\s+best\b",
    ]
    for pattern in research_only:
        if re.search(pattern, text):
            return "RESEARCH"

    # Strong signals for PLANNING only
    planning_only = [
        r"\bplan\b.*\bnot\b.*\bimplement\b",
        r"\barchitect(?:ure)?\b.*\bonly\b",
        r"\bdesign\b.*\bno\s+code\b",
        r"\broadmap\b",
    ]
    for pattern in planning_only:
        if re.search(pattern, text):
            return "PLANNING"

    # Strong signals for IMPLEMENTATION
    implementation_signals = [
        r"\bbuild\b", r"\bcreate\b", r"\bimplement\b",
        r"\bdevelop\b", r"\bwrite\b.*\bcode\b",
        r"\bapp\b", r"\bcomponent\b", r"\bscript\b",
        r"\bapi\b", r"\bservice\b", r"\bplugin\b",
    ]
    impl_score = sum(1 for p in implementation_signals if re.search(p, text))

    research_signals = [
        r"\bresearch", r"\bcompare", r"\banalyz",
        r"\binvestigat", r"\bevaluat",
    ]
    research_score = sum(1 for p in research_signals if re.search(p, text))

    if impl_score > 0 and research_score > 0:
        return "HYBRID"
    if impl_score > 0:
        return "IMPLEMENTATION"
    if research_score > 0:
        return "RESEARCH"

    # Default: if we can't tell, assume implementation (most common request)
    return "IMPLEMENTATION"
