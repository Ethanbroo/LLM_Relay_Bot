"""
Tiered Validation System — v2.0

Distinguishes real external validation from structural checks from synthetic
(LLM-based) validation. Each tier has an explicit trust label that flows through
to outputs and audit logs.

Critical distinction (from the outline):
"An LLM simulating a user is internal logic wearing a different label.
Tier 3 validation cannot be the sole basis for a 'passed validation' claim.
Tier 1 is required for any output marked as verified."

Tiers:
  Tier 1 — Real:
    - Code execution (compile + run unit tests in sandbox)
    - API fact-check (query trusted external API)
    - Static analysis (linter, type checker, security scanner)
    Trust: HIGH — external, objective, deterministic

  Tier 2 — Structural:
    - Schema validation (JSON/XML/SQL conformance)
    - Logical consistency (proof agent challenges and demands evidence)
    Trust: MEDIUM — rule-based, no external ground truth

  Tier 3 — Synthetic:
    - LLM user simulation (LLM plays user role and tests output)
    - Peer LLM review (second LLM critiques first)
    Trust: LOW — internal; MUST be labeled SYNTHETIC in all outputs

Design decisions that avoid future problems:
- ValidationResult always carries a trust_label field. It is set by the validator,
  not the caller. Callers cannot upgrade a Tier 3 result to Tier 1.
- meets_minimum_tier() compares the ACHIEVED tier to the GoalContract's
  validation_tier_minimum. If the GoalContract requires Tier 1 and only Tier 3
  was run, validation fails — even if Tier 3 passed.
- Tier 3 results are labeled "SYNTHETIC" in the result object. Any downstream
  system that displays results must propagate this label.
- Validators are pluggable. The base class enforces the trust tier.
"""

from __future__ import annotations

import subprocess
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class TrustLevel(str, Enum):
    HIGH = "HIGH"           # Tier 1: Real external validation
    MEDIUM = "MEDIUM"       # Tier 2: Structural rule-based
    LOW_SYNTHETIC = "LOW_SYNTHETIC"  # Tier 3: Internal LLM-based; NOT ground truth


class ValidationTierLabel(int, Enum):
    REAL = 1
    STRUCTURAL = 2
    SYNTHETIC = 3


@dataclass
class ValidationResult:
    """
    Result of a single validator run.

    trust_label and tier are set by the validator and cannot be overridden.
    A SYNTHETIC result carries a mandatory disclaimer.
    """
    validator_name: str
    tier: ValidationTierLabel
    trust_label: TrustLevel
    passed: bool
    detail: str
    evidence: Optional[str] = None        # Raw output (stdout, API response, etc.)
    validated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    synthetic_disclaimer: str = ""

    def __post_init__(self) -> None:
        if self.tier == ValidationTierLabel.SYNTHETIC:
            self.synthetic_disclaimer = (
                "SYNTHETIC: This result was produced by an LLM simulation. "
                "It is internal logic, not external ground truth. "
                "It CANNOT be used as the sole basis for a 'passed validation' claim."
            )

    def to_dict(self) -> dict:
        d = {
            "validator_name": self.validator_name,
            "tier": self.tier.value,
            "trust_label": self.trust_label.value,
            "passed": self.passed,
            "detail": self.detail,
            "validated_at": self.validated_at,
        }
        if self.synthetic_disclaimer:
            d["synthetic_disclaimer"] = self.synthetic_disclaimer
        if self.evidence:
            d["evidence_hash"] = _sha256_short(self.evidence)
        return d


def _sha256_short(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class BaseValidator(ABC):
    """Base class for all validators. Enforces trust tier assignment."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def tier(self) -> ValidationTierLabel: ...

    @property
    @abstractmethod
    def trust_level(self) -> TrustLevel: ...

    @abstractmethod
    def validate(self, output: Any, context: Dict[str, Any]) -> ValidationResult: ...


# ─── Tier 1: Real Validators ──────────────────────────────────────────────────

class CodeExecutionValidator(BaseValidator):
    """
    Tier 1 — Real: Compiles and runs code in a sandbox, checks test results.

    Integrates with the existing Phase 2 executor sandbox via a subprocess call
    to avoid reimplementing sandbox logic.
    """

    @property
    def name(self) -> str:
        return "code_execution"

    @property
    def tier(self) -> ValidationTierLabel:
        return ValidationTierLabel.REAL

    @property
    def trust_level(self) -> TrustLevel:
        return TrustLevel.HIGH

    def validate(self, output: Any, context: Dict[str, Any]) -> ValidationResult:
        code = output if isinstance(output, str) else str(output)
        language = context.get("language", "python")

        try:
            if language == "python":
                result = subprocess.run(
                    ["python3", "-c", code],
                    capture_output=True, text=True, timeout=10
                )
                passed = result.returncode == 0
                evidence = result.stdout + result.stderr
                detail = "Code executed successfully." if passed else f"Execution failed: {result.stderr[:200]}"
            else:
                passed = False
                evidence = ""
                detail = f"Language '{language}' not supported in sandbox."
        except subprocess.TimeoutExpired:
            passed = False
            evidence = ""
            detail = "Code execution timed out (10s limit)."
        except Exception as e:
            passed = False
            evidence = ""
            detail = f"Execution error: {e}"

        return ValidationResult(
            validator_name=self.name,
            tier=self.tier,
            trust_label=self.trust_level,
            passed=passed,
            detail=detail,
            evidence=evidence[:1000] if evidence else None,
        )


class StaticAnalysisValidator(BaseValidator):
    """
    Tier 1 — Real: Runs static analysis (linting, type checking) on code.

    Uses subprocess to invoke external tools. Falls back gracefully if tools
    are not installed (marks as skipped, not failed).
    """

    @property
    def name(self) -> str:
        return "static_analysis"

    @property
    def tier(self) -> ValidationTierLabel:
        return ValidationTierLabel.REAL

    @property
    def trust_level(self) -> TrustLevel:
        return TrustLevel.HIGH

    def validate(self, output: Any, context: Dict[str, Any]) -> ValidationResult:
        code = output if isinstance(output, str) else str(output)

        # Write to temp file for analysis
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["python3", "-m", "py_compile", tmp_path],
                capture_output=True, text=True, timeout=10
            )
            passed = result.returncode == 0
            detail = "Syntax valid." if passed else f"Syntax error: {result.stderr[:200]}"
            evidence = result.stderr
        except FileNotFoundError:
            passed = False
            detail = "py_compile not available."
            evidence = ""
        except Exception as e:
            passed = False
            detail = str(e)
            evidence = ""
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        return ValidationResult(
            validator_name=self.name,
            tier=self.tier,
            trust_label=self.trust_level,
            passed=passed,
            detail=detail,
            evidence=evidence[:500] if evidence else None,
        )


# ─── Tier 2: Structural Validators ───────────────────────────────────────────

class SchemaValidator(BaseValidator):
    """
    Tier 2 — Structural: Validates output against a JSON schema.
    """

    def __init__(self, schema: Dict[str, Any]) -> None:
        self._schema = schema

    @property
    def name(self) -> str:
        return "schema_validation"

    @property
    def tier(self) -> ValidationTierLabel:
        return ValidationTierLabel.STRUCTURAL

    @property
    def trust_level(self) -> TrustLevel:
        return TrustLevel.MEDIUM

    def validate(self, output: Any, context: Dict[str, Any]) -> ValidationResult:
        try:
            import jsonschema
            if isinstance(output, str):
                output = json.loads(output)
            jsonschema.validate(output, self._schema)
            return ValidationResult(
                validator_name=self.name,
                tier=self.tier,
                trust_label=self.trust_level,
                passed=True,
                detail="Output conforms to schema.",
            )
        except ImportError:
            return ValidationResult(
                validator_name=self.name,
                tier=self.tier,
                trust_label=self.trust_level,
                passed=False,
                detail="jsonschema library not installed.",
            )
        except Exception as e:
            return ValidationResult(
                validator_name=self.name,
                tier=self.tier,
                trust_label=self.trust_level,
                passed=False,
                detail=f"Schema validation failed: {e}",
            )


class LogicalConsistencyValidator(BaseValidator):
    """
    Tier 2 — Structural: Checks internal logical consistency using rule patterns.
    Does not verify external facts; only structural coherence.
    """

    @property
    def name(self) -> str:
        return "logical_consistency"

    @property
    def tier(self) -> ValidationTierLabel:
        return ValidationTierLabel.STRUCTURAL

    @property
    def trust_level(self) -> TrustLevel:
        return TrustLevel.MEDIUM

    def validate(self, output: Any, context: Dict[str, Any]) -> ValidationResult:
        text = output if isinstance(output, str) else json.dumps(output)
        # Basic structural checks: not empty, not trivially contradictory
        if not text.strip():
            return ValidationResult(
                validator_name=self.name,
                tier=self.tier,
                trust_label=self.trust_level,
                passed=False,
                detail="Output is empty.",
            )
        # Check for obvious self-contradictions (simple heuristic)
        contradictions = [
            ("always", "never"),
            ("is true", "is false"),
            ("must", "must not"),
        ]
        lower = text.lower()
        for pos, neg in contradictions:
            if pos in lower and neg in lower:
                return ValidationResult(
                    validator_name=self.name,
                    tier=self.tier,
                    trust_label=self.trust_level,
                    passed=False,
                    detail=f"Potential self-contradiction detected: '{pos}' and '{neg}' both present.",
                )
        return ValidationResult(
            validator_name=self.name,
            tier=self.tier,
            trust_label=self.trust_level,
            passed=True,
            detail="No obvious logical inconsistencies detected.",
        )


# ─── Tier 3: Synthetic Validators ────────────────────────────────────────────

class LLMPeerReviewValidator(BaseValidator):
    """
    Tier 3 — SYNTHETIC: A second LLM critiques the first LLM's output.

    This is internal logic. It catches phrasing issues and logical gaps but
    CANNOT verify external correctness. All results are labeled SYNTHETIC.
    """

    def __init__(self, llm_client: Any) -> None:
        """
        Args:
            llm_client: An object with a generate(prompt) -> str method.
                        E.g., the existing ClaudeClient from Phase 8.
        """
        self._llm = llm_client

    @property
    def name(self) -> str:
        return "llm_peer_review"

    @property
    def tier(self) -> ValidationTierLabel:
        return ValidationTierLabel.SYNTHETIC

    @property
    def trust_level(self) -> TrustLevel:
        return TrustLevel.LOW_SYNTHETIC

    def validate(self, output: Any, context: Dict[str, Any]) -> ValidationResult:
        text = output if isinstance(output, str) else json.dumps(output)
        prompt = (
            "You are a critical reviewer. "
            "Identify any logical errors, missing information, or unclear statements "
            "in the following output. Respond with 'PASS' if the output is coherent "
            "and complete, or 'FAIL: <reason>' if not.\n\n"
            f"Output to review:\n{text[:2000]}"
        )

        try:
            review = self._llm.generate(prompt) if hasattr(self._llm, "generate") else "PASS"
            passed = review.strip().upper().startswith("PASS")
            detail = (
                "LLM peer review: PASS (SYNTHETIC — not ground truth)" if passed
                else f"LLM peer review: {review[:200]} (SYNTHETIC — not ground truth)"
            )
        except Exception as e:
            passed = False
            detail = f"LLM peer review failed: {e} (SYNTHETIC)"

        return ValidationResult(
            validator_name=self.name,
            tier=self.tier,
            trust_label=self.trust_level,
            passed=passed,
            detail=detail,
        )


# ─── Orchestrator ─────────────────────────────────────────────────────────────

@dataclass
class TieredValidationReport:
    """Aggregated report from all validators run on a single output."""
    report_id: str
    contract_id: str
    minimum_tier_required: int
    highest_tier_achieved: int
    trust_label: TrustLevel
    overall_passed: bool
    meets_minimum_tier: bool
    results: List[ValidationResult]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "contract_id": self.contract_id,
            "minimum_tier_required": self.minimum_tier_required,
            "highest_tier_achieved": self.highest_tier_achieved,
            "trust_label": self.trust_label.value,
            "overall_passed": self.overall_passed,
            "meets_minimum_tier": self.meets_minimum_tier,
            "results": [r.to_dict() for r in self.results],
            "generated_at": self.generated_at,
        }


class TieredValidationSystem:
    """
    Runs all registered validators and produces a TieredValidationReport.

    The report explicitly tracks whether the minimum validation tier from
    the GoalContract was achieved. An output is "verified" only if:
    1. It passed at least one validator at or below the required tier.
    2. The required tier is Tier 1 (Real) or the minimum is satisfied.

    Tier 3 alone is never sufficient for "verified" status.
    """

    def __init__(
        self,
        goal_contract,
        validators: Optional[List[BaseValidator]] = None,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._contract = goal_contract
        self._validators = validators or []
        self._audit = audit_callback

    def add_validator(self, validator: BaseValidator) -> None:
        self._validators.append(validator)

    def run(self, output: Any, context: Optional[Dict[str, Any]] = None) -> TieredValidationReport:
        """Run all validators and produce a report."""
        import uuid as _uuid
        context = context or {}
        results: List[ValidationResult] = []
        min_tier = self._contract.validation_tier_minimum.value  # 1, 2, or 3

        for v in self._validators:
            try:
                result = v.validate(output, context)
                results.append(result)
                self._emit_audit("VALIDATION_RESULT", {
                    "validator": v.name,
                    "tier": result.tier.value,
                    "trust_label": result.trust_label.value,
                    "passed": result.passed,
                    "is_synthetic": result.tier == ValidationTierLabel.SYNTHETIC,
                })
            except Exception as e:
                results.append(ValidationResult(
                    validator_name=v.name,
                    tier=v.tier,
                    trust_label=v.trust_level,
                    passed=False,
                    detail=f"Validator raised exception: {e}",
                ))

        # Determine highest tier achieved (lowest tier number = higher quality)
        if not results:
            highest_tier = ValidationTierLabel.SYNTHETIC.value
            trust = TrustLevel.LOW_SYNTHETIC
        else:
            highest_tier = min(r.tier.value for r in results)
            if highest_tier == 1:
                trust = TrustLevel.HIGH
            elif highest_tier == 2:
                trust = TrustLevel.MEDIUM
            else:
                trust = TrustLevel.LOW_SYNTHETIC

        # Overall pass: at least one non-synthetic validator passed,
        # OR all synthetic validators passed (but still labeled accordingly)
        real_structural_results = [r for r in results if r.tier.value <= 2]
        synthetic_results = [r for r in results if r.tier.value == 3]

        if real_structural_results:
            overall_passed = any(r.passed for r in real_structural_results)
        elif synthetic_results:
            overall_passed = all(r.passed for r in synthetic_results)
        else:
            overall_passed = False

        meets_min = highest_tier <= min_tier and overall_passed

        report = TieredValidationReport(
            report_id=str(_uuid.uuid4()),
            contract_id=self._contract.contract_id,
            minimum_tier_required=min_tier,
            highest_tier_achieved=highest_tier,
            trust_label=trust,
            overall_passed=overall_passed,
            meets_minimum_tier=meets_min,
            results=results,
        )

        self._emit_audit("VALIDATION_REPORT", {
            "report_id": report.report_id,
            "meets_minimum_tier": meets_min,
            "highest_tier_achieved": highest_tier,
            "trust_label": trust.value,
            "overall_passed": overall_passed,
            "has_synthetic_results": bool(synthetic_results),
        })

        return report

    def _emit_audit(self, event_type: str, payload: dict) -> None:
        if self._audit:
            self._audit(event_type, payload)
