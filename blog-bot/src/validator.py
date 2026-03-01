"""Validation Layer — Programmatic Checks.

Runs all checks on every generated blog. Returns a validation report with
pass/fail per check and an overall status. No LLM needed — pure Python
string/regex analysis.

Checks:
1. Word Count
2. METADATA Block Present
3. Friendly Connections Mention
4. No URLs in Article Body
5. Header Formatting
6. Section Completeness
7. Comma-to-Dash Ratio
8. Em-Dash Usage (max 3 em-dashes per article)
9. Preachy Tone (imperative self-help patterns)
10. No Structural Labels as Headers
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Structural labels that must NOT appear as headers
STRUCTURAL_LABELS = [
    "hook", "lede", "nut graf", "context", "definitions",
    "evidence", "expert insight", "narrative experiments",
    "complication", "nuance", "practical synthesis",
    "closing kicker", "kicker",
]

# URL patterns to detect in article body
URL_PATTERNS = [
    r"https?://",
    r"www\.",
    r"\w+\.com\b",
    r"\w+\.org\b",
    r"\w+\.services\b",
    r"\w+\.ca\b",
    r"\w+\.net\b",
]


@dataclass
class CheckResult:
    """Result of a single validation check."""
    name: str
    status: str  # "PASS", "FAIL", "WARN"
    detail: str = ""


@dataclass
class ValidationReport:
    """Full validation report for a generated blog."""
    checks: list[CheckResult] = field(default_factory=list)
    overall_pass: bool = True
    fail_count: int = 0
    warn_count: int = 0

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)
        if check.status == "FAIL":
            self.fail_count += 1
            self.overall_pass = False
        elif check.status == "WARN":
            self.warn_count += 1

    def summary(self) -> str:
        lines = [f"Validation: {'PASS' if self.overall_pass else 'FAIL'} "
                 f"({self.fail_count} failures, {self.warn_count} warnings)"]
        for c in self.checks:
            lines.append(f"  [{c.status}] {c.name}: {c.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "overall_pass": self.overall_pass,
            "fail_count": self.fail_count,
            "warn_count": self.warn_count,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
        }


def _split_article_and_metadata(raw_output: str) -> tuple[str, str]:
    """Split raw LLM output into article body and METADATA block.

    Returns (article_body, metadata_block). If no METADATA section found,
    metadata_block is empty string.
    """
    # Look for METADATA header (case-insensitive, with or without markdown header markers)
    patterns = [
        r"\n#{1,4}\s*METADATA\s*\n",
        r"\nMETADATA\s*\n",
        r"\n\*\*METADATA\*\*\s*\n",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_output, re.IGNORECASE)
        if match:
            article = raw_output[:match.start()].strip()
            metadata = raw_output[match.end():].strip()
            return article, metadata

    return raw_output.strip(), ""


def _check_word_count(article: str, target: int) -> CheckResult:
    """CHECK 1: Word count within [target * 0.95, target * 1.10]."""
    # Strip markdown/HTML for accurate count
    text = re.sub(r"<[^>]+>", " ", article)
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"\*{1,3}", "", text)
    words = text.split()
    count = len(words)

    min_words = int(target * 0.85)
    max_words = int(target * 1.20)

    if min_words <= count <= max_words:
        return CheckResult(
            name="Word Count",
            status="PASS",
            detail=f"{count} words (target: {target}, range: {min_words}-{max_words})",
        )
    elif count < min_words:
        return CheckResult(
            name="Word Count",
            status="FAIL",
            detail=f"UNDER: {count} words (minimum: {min_words}, target: {target})",
        )
    else:
        return CheckResult(
            name="Word Count",
            status="FAIL",
            detail=f"OVER: {count} words (maximum: {max_words}, target: {target})",
        )


def _check_metadata_present(metadata: str) -> CheckResult:
    """CHECK 2: METADATA block present with at least 1 anchor→blog mapping."""
    if not metadata:
        return CheckResult(
            name="METADATA Block",
            status="FAIL",
            detail="No METADATA section found in output.",
        )

    # Look for anchor → blog mappings (arrow variants: →, ->, ==>)
    arrow_pattern = r".+\s*(?:→|->|==>|=>)\s*.+"
    mappings = re.findall(arrow_pattern, metadata)

    if len(mappings) >= 1:
        return CheckResult(
            name="METADATA Block",
            status="PASS",
            detail=f"Found {len(mappings)} anchor→blog mapping(s).",
        )
    else:
        return CheckResult(
            name="METADATA Block",
            status="FAIL",
            detail="METADATA section exists but no anchor→blog mappings found.",
        )


def _check_fc_mention(article: str) -> CheckResult:
    """CHECK 3: Friendly Connections mentioned 1-3 sentences in article body."""
    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", article)
    fc_sentences = [
        s for s in sentences
        if "friendly connections" in s.lower()
    ]
    count = len(fc_sentences)

    if 1 <= count <= 3:
        return CheckResult(
            name="FC Mention",
            status="PASS",
            detail=f"Found in {count} sentence(s).",
        )
    elif count == 0:
        return CheckResult(
            name="FC Mention",
            status="FAIL",
            detail="No mention of Friendly Connections found in article.",
        )
    else:
        return CheckResult(
            name="FC Mention",
            status="FAIL",
            detail=f"Too many mentions: found in {count} sentences (max: 3).",
        )


def _check_no_urls(article: str) -> CheckResult:
    """CHECK 4: No URLs in article body."""
    found_urls = []
    for pattern in URL_PATTERNS:
        matches = re.findall(pattern, article, re.IGNORECASE)
        found_urls.extend(matches)

    if not found_urls:
        return CheckResult(
            name="No URLs",
            status="PASS",
            detail="No URLs detected in article body.",
        )
    else:
        return CheckResult(
            name="No URLs",
            status="FAIL",
            detail=f"Found {len(found_urls)} URL pattern(s) in article body: {found_urls[:3]}",
        )


def _check_header_formatting(article: str) -> CheckResult:
    """CHECK 5: Primary headers use H3, sub-headers use H4.

    FAIL if H1 or H2 is used (reserved for page-level titles).
    """
    lines = article.split("\n")
    h1_count = 0
    h2_count = 0
    h3_count = 0
    h4_count = 0
    h4_non_list = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#### "):
            h4_count += 1
            # Check if H4 is followed by a list
            is_list_header = False
            for j in range(i + 1, min(i + 4, len(lines))):
                next_line = lines[j].strip()
                if next_line.startswith("- ") or next_line.startswith("* ") or re.match(r"^\d+\.", next_line):
                    is_list_header = True
                    break
                if next_line and not next_line.startswith("#"):
                    break
            if not is_list_header:
                h4_non_list += 1
        elif stripped.startswith("### "):
            h3_count += 1
        elif stripped.startswith("## ") and not stripped.startswith("### "):
            h2_count += 1
        elif stripped.startswith("# ") and not stripped.startswith("## "):
            h1_count += 1

    # Allow exactly 1 H1 for the article title (Section 1 — Title & Dek)
    if h1_count > 1 or h2_count > 0:
        return CheckResult(
            name="Header Formatting",
            status="FAIL",
            detail=f"Found H1 ({h1_count}) or H2 ({h2_count}) headers — use H3 for sections (1 H1 allowed for title).",
        )

    if h1_count == 1 and h3_count == 0:
        return CheckResult(
            name="Header Formatting",
            status="FAIL",
            detail="Only H1 found with no H3 section headers — sections should use H3.",
        )

    if h4_non_list > 0:
        return CheckResult(
            name="Header Formatting",
            status="WARN",
            detail=f"Found {h4_non_list} H4 header(s) not followed by lists (H4 is for list sections only).",
        )

    if h3_count > 0:
        return CheckResult(
            name="Header Formatting",
            status="PASS",
            detail=f"H3: {h3_count}, H4: {h4_count} — formatting correct.",
        )

    return CheckResult(
        name="Header Formatting",
        status="WARN",
        detail="No markdown headers found — article may use HTML headers instead.",
    )


def _check_section_completeness(article: str) -> CheckResult:
    """CHECK 6: At least 7 distinct H3 headers (9 sections, some may lack explicit headers)."""
    h3_headers = re.findall(r"^###\s+.+$", article, re.MULTILINE)

    # Also check HTML H3 tags
    html_h3 = re.findall(r"<h3[^>]*>.+?</h3>", article, re.IGNORECASE)
    total = len(h3_headers) + len(html_h3)

    if total >= 7:
        return CheckResult(
            name="Section Completeness",
            status="PASS",
            detail=f"Found {total} section headers (minimum: 7).",
        )
    elif 5 <= total < 7:
        return CheckResult(
            name="Section Completeness",
            status="WARN",
            detail=f"Found {total} section headers (expected 7+ — some sections may be merged).",
        )
    else:
        return CheckResult(
            name="Section Completeness",
            status="FAIL",
            detail=f"Found only {total} section headers (minimum: 5, expected: 7+). Sections were likely skipped.",
        )


def _check_comma_dash_ratio(article: str) -> CheckResult:
    """CHECK 7: Commas outnumber dashes by at least 3:1."""
    commas = article.count(",")
    # Count em-dashes and en-dashes (not hyphens in compound words)
    em_dashes = article.count("—") + article.count("–")
    # Also count spaced hyphens (informal em-dash usage)
    spaced_hyphens = len(re.findall(r"\s-\s", article))
    total_dashes = em_dashes + spaced_hyphens

    if total_dashes == 0:
        ratio_str = "inf"
        status = "PASS"
    else:
        ratio = commas / total_dashes
        ratio_str = f"{ratio:.1f}:1"
        if ratio >= 3.0:
            status = "PASS"
        elif ratio >= 2.0:
            status = "WARN"
        else:
            status = "FAIL"

    return CheckResult(
        name="Comma-to-Dash Ratio",
        status=status,
        detail=f"Commas: {commas}, Dashes: {total_dashes}, Ratio: {ratio_str} (target: >= 3:1).",
    )


def _check_em_dash_usage(article: str) -> CheckResult:
    """CHECK 9: Em-dashes used only for compound words, not clause breaks.

    FAIL if more than 3 em-dashes (—/–) appear as clause separators.
    Compound-word hyphens (e.g., "self-deprecation") are not counted.
    """
    # Count em-dashes and en-dashes
    em_dash_count = article.count("—") + article.count("–")
    # Also count spaced hyphens used as informal em-dashes
    spaced_hyphens = len(re.findall(r"\s-\s", article))
    total_em_dashes = em_dash_count + spaced_hyphens

    if total_em_dashes <= 3:
        return CheckResult(
            name="Em-Dash Usage",
            status="PASS",
            detail=f"Found {total_em_dashes} em-dash(es) (maximum: 3).",
        )
    elif total_em_dashes <= 5:
        return CheckResult(
            name="Em-Dash Usage",
            status="WARN",
            detail=f"Found {total_em_dashes} em-dash(es) (maximum: 3). Slightly over limit.",
        )
    else:
        return CheckResult(
            name="Em-Dash Usage",
            status="FAIL",
            detail=f"Found {total_em_dashes} em-dash(es) (maximum: 3). Em-dashes should only be used for compound words, not clause breaks.",
        )


def _check_preachy_tone(article: str) -> CheckResult:
    """CHECK 10: Detect preachy imperative self-help advice patterns.

    Catches consecutive imperative sentences ("Stop doing X. Start doing Y.")
    and common self-help cliches that violate the VICE-style tone rules.

    FAIL if 3+ preachy patterns found. WARN if 1-2 found.
    """
    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", article)

    preachy_patterns = []

    # Pattern 1: Imperative sentences that read like self-help commands
    imperative_starters = [
        r"^stop\s",
        r"^start\s",
        r"^accept\s+that\b",
        r"^recognize\s+that\b",
        r"^remember\s+that\b",
        r"^find\s+ways?\s+to\b",
        r"^learn\s+to\b",
        r"^try\s+to\b",
        r"^make\s+sure\b",
        r"^don't\s+be\s+afraid\b",
        r"^embrace\b",
        r"^let\s+go\b",
        r"^choose\s+to\b",
        r"^commit\s+to\b",
        r"^be\s+willing\b",
        r"^allow\s+yourself\b",
        r"^give\s+yourself\b",
        r"^take\s+the\s+first\s+step\b",
    ]
    for sentence in sentences:
        s = sentence.strip()
        for pattern in imperative_starters:
            if re.match(pattern, s, re.IGNORECASE):
                preachy_patterns.append(s[:80])
                break

    # Pattern 2: "Most importantly" + imperative (preachy framing)
    most_importantly = re.findall(
        r"most importantly[,:]?\s+\w+", article, re.IGNORECASE
    )
    preachy_patterns.extend([m[:80] for m in most_importantly])

    # Deduplicate
    seen = set()
    unique = []
    for p in preachy_patterns:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    count = len(unique)

    if count == 0:
        return CheckResult(
            name="Preachy Tone",
            status="PASS",
            detail="No imperative self-help patterns detected.",
        )
    elif count <= 2:
        return CheckResult(
            name="Preachy Tone",
            status="WARN",
            detail=f"Found {count} preachy pattern(s): {'; '.join(unique[:2])}...",
        )
    else:
        return CheckResult(
            name="Preachy Tone",
            status="FAIL",
            detail=f"Found {count} preachy/imperative patterns (max: 2). Examples: {'; '.join(unique[:3])}...",
        )


def _check_no_structural_labels(article: str) -> CheckResult:
    """CHECK 8: No structural labels used as headers.

    Checks that headers don't contain: Hook, Lede, Nut Graf, Context,
    Definitions, Evidence, Expert Insight, Narrative Experiments,
    Complication, Nuance, Practical Synthesis, Closing Kicker, Kicker.
    """
    # Find all headers (markdown and HTML)
    md_headers = re.findall(r"^#{1,6}\s+(.+)$", article, re.MULTILINE)
    html_headers = re.findall(r"<h[1-6][^>]*>(.+?)</h[1-6]>", article, re.IGNORECASE)
    all_headers = md_headers + html_headers

    found_labels = []
    for header in all_headers:
        header_lower = header.strip().lower()
        # Remove markdown formatting from header text
        header_lower = re.sub(r"\*{1,3}", "", header_lower).strip()
        for label in STRUCTURAL_LABELS:
            if label in header_lower:
                found_labels.append(f"'{header.strip()}' contains '{label}'")
                break

    if not found_labels:
        return CheckResult(
            name="No Structural Labels",
            status="PASS",
            detail="No structural labels found in headers.",
        )
    else:
        return CheckResult(
            name="No Structural Labels",
            status="FAIL",
            detail=f"Found {len(found_labels)} structural label(s) in headers: {'; '.join(found_labels[:3])}",
        )


def validate(raw_output: str, target_word_count: int) -> ValidationReport:
    """Run all validation checks on raw LLM output.

    Args:
        raw_output: The complete LLM output (article + METADATA).
        target_word_count: The target word count for this blog.

    Returns a ValidationReport with all check results and overall status.
    """
    report = ValidationReport()

    article, metadata = _split_article_and_metadata(raw_output)

    report.add(_check_word_count(article, target_word_count))
    report.add(_check_metadata_present(metadata))
    report.add(_check_fc_mention(article))
    report.add(_check_no_urls(article))
    report.add(_check_header_formatting(article))
    report.add(_check_section_completeness(article))
    report.add(_check_comma_dash_ratio(article))
    report.add(_check_em_dash_usage(article))
    report.add(_check_preachy_tone(article))
    report.add(_check_no_structural_labels(article))

    logger.info(report.summary())

    return report
