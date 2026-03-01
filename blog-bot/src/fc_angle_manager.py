"""FC Integration Angle Manager — Two-Phase System.

Manages how Friendly Connections is mentioned in each blog. Implements a
two-phase approach that starts manual and transitions to a curated template
library once enough patterns emerge.

Phase 1 (Active until 15+ angles accumulated):
  - Requires manual fc_angle input for each blog.
  - Every manual angle is logged with type tag and metadata.

Phase 2 (Activates after 15+ angles with 3+ type tags, manually confirmed):
  - Auto-selects from template library built from logged patterns.
  - Manual override always available.
  - Displays selected angle for review before generation.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
FC_ANGLES_PATH = CONFIG_DIR / "fc_angles.json"

# Valid type tags for FC angles
VALID_TYPES = [
    "convenience",
    "problem_solution",
    "explanation",
    "trend",
    "cultural_shift",
    "comparison",
    "other",
]

# Minimum angles before Phase 2 can be activated
PHASE_2_MIN_ANGLES = 15
PHASE_2_MIN_TYPES = 3

# Default templates seeded when Phase 2 activates
DEFAULT_TEMPLATES = [
    {
        "type": "convenience",
        "template": (
            "Mention Friendly Connections as an example of services that handle "
            "the logistics of {social_challenge}, so readers can skip the "
            "{barrier} phase."
        ),
        "variables": ["social_challenge", "barrier"],
        "example_fills": {
            "social_challenge": "meeting new people in a new city",
            "barrier": "awkward cold-approach",
        },
        "times_used": 0,
        "last_used": None,
    },
    {
        "type": "problem_solution",
        "template": (
            "Reference Friendly Connections when the article discusses "
            "{problem_described}, positioning it as a service built around "
            "solving exactly that problem."
        ),
        "variables": ["problem_described"],
        "example_fills": {
            "problem_described": "the difficulty of finding activity partners as an adult",
        },
        "times_used": 0,
        "last_used": None,
    },
    {
        "type": "trend",
        "template": (
            "Introduce Friendly Connections in the context of {cultural_trend}, "
            "framing it as part of a broader shift toward {broader_movement}."
        ),
        "variables": ["cultural_trend", "broader_movement"],
        "example_fills": {
            "cultural_trend": "paid companionship becoming destigmatized",
            "broader_movement": (
                "people investing in their social health the same way "
                "they invest in fitness"
            ),
        },
        "times_used": 0,
        "last_used": None,
    },
    {
        "type": "explanation",
        "template": (
            "When explaining {concept}, mention Friendly Connections as a "
            "real-world example of how {practical_application}."
        ),
        "variables": ["concept", "practical_application"],
        "example_fills": {
            "concept": "the difference between social contact and genuine connection",
            "practical_application": (
                "structured social activities can bridge that gap"
            ),
        },
        "times_used": 0,
        "last_used": None,
    },
    {
        "type": "cultural_shift",
        "template": (
            "Position Friendly Connections within the broader cultural shift of "
            "{shift_description}, noting that {observation}."
        ),
        "variables": ["shift_description", "observation"],
        "example_fills": {
            "shift_description": (
                "people paying for experiences that used to happen organically"
            ),
            "observation": (
                "this isn't laziness, it's adaptation to a world where "
                "organic social infrastructure has eroded"
            ),
        },
        "times_used": 0,
        "last_used": None,
    },
    {
        "type": "comparison",
        "template": (
            "When the article compares {approaches}, introduce Friendly "
            "Connections as one option among several, noting that it "
            "{differentiator}."
        ),
        "variables": ["approaches", "differentiator"],
        "example_fills": {
            "approaches": "different ways to meet people as an adult",
            "differentiator": (
                "focuses specifically on platonic connection rather than "
                "dating or networking"
            ),
        },
        "times_used": 0,
        "last_used": None,
    },
]

# Topic keyword → type mapping for auto-selection
TOPIC_TYPE_MAP = {
    "convenience": [
        "how to", "easy", "simple", "logistics", "start", "begin",
        "where to", "finding", "meeting",
    ],
    "problem_solution": [
        "hard", "difficult", "struggle", "impossible", "can't",
        "why is it", "challenge", "problem", "stuck",
    ],
    "trend": [
        "trend", "rise of", "growing", "epidemic", "generation",
        "2025", "2026", "modern", "new era",
    ],
    "cultural_shift": [
        "changing", "shift", "evolving", "used to", "no longer",
        "society", "culture", "norms",
    ],
    "comparison": [
        "versus", "vs", "compared", "better", "alternative",
        "options", "ways to", "methods",
    ],
    "explanation": [
        "what is", "why do", "science", "psychology", "research",
        "understanding", "definition", "meaning",
    ],
}


def _load_angles() -> dict:
    """Load FC angles data from disk."""
    if FC_ANGLES_PATH.exists():
        return json.loads(FC_ANGLES_PATH.read_text())
    data = {
        "angles": [],
        "templates": [],
        "phase": 1,
        "phase_2_confirmed": False,
    }
    _save_angles(data)
    return data


def _save_angles(data: dict) -> None:
    """Save FC angles data to disk."""
    FC_ANGLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FC_ANGLES_PATH.write_text(json.dumps(data, indent=2))


def get_phase() -> int:
    """Return the current phase (1 or 2)."""
    data = _load_angles()
    if data.get("phase_2_confirmed") and data.get("phase") == 2:
        return 2
    return 1


def _count_type_tags(angles: list[dict]) -> dict[str, int]:
    """Count how many angles exist per type tag."""
    counts: dict[str, int] = {}
    for angle in angles:
        tag = angle.get("type", "other")
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def is_phase_2_eligible() -> dict:
    """Check if the system is eligible to transition to Phase 2.

    Returns a dict with eligibility status and details.
    """
    data = _load_angles()
    angles = data.get("angles", [])
    type_counts = _count_type_tags(angles)

    eligible = (
        len(angles) >= PHASE_2_MIN_ANGLES
        and len(type_counts) >= PHASE_2_MIN_TYPES
    )

    return {
        "eligible": eligible,
        "total_angles": len(angles),
        "required_angles": PHASE_2_MIN_ANGLES,
        "type_tags": type_counts,
        "unique_types": len(type_counts),
        "required_types": PHASE_2_MIN_TYPES,
        "already_confirmed": data.get("phase_2_confirmed", False),
    }


def confirm_phase_2() -> bool:
    """Manually confirm activation of Phase 2.

    Returns True if Phase 2 was activated, False if not eligible.
    """
    eligibility = is_phase_2_eligible()
    if not eligibility["eligible"]:
        logger.warning(
            "Cannot activate Phase 2: need %d angles (have %d) and %d types (have %d).",
            PHASE_2_MIN_ANGLES,
            eligibility["total_angles"],
            PHASE_2_MIN_TYPES,
            eligibility["unique_types"],
        )
        return False

    data = _load_angles()
    data["phase"] = 2
    data["phase_2_confirmed"] = True
    data["phase_2_confirmed_at"] = datetime.utcnow().isoformat()

    # Seed default templates if none exist
    if not data.get("templates"):
        data["templates"] = DEFAULT_TEMPLATES
        logger.info("Seeded %d default FC angle templates.", len(DEFAULT_TEMPLATES))

    _save_angles(data)
    logger.info("Phase 2 activated with %d angles logged.", eligibility["total_angles"])
    return True


def log_angle(
    angle_text: str,
    blog_title: str,
    angle_type: str = "other",
) -> None:
    """Log a manual FC angle to the library.

    Args:
        angle_text: The angle instruction text.
        blog_title: The blog title this angle was used for.
        angle_type: One of the VALID_TYPES tags.
    """
    if angle_type not in VALID_TYPES:
        logger.warning(
            "Invalid angle type '%s' — defaulting to 'other'. Valid: %s",
            angle_type,
            VALID_TYPES,
        )
        angle_type = "other"

    data = _load_angles()

    # Deduplicate: skip if exact same angle text already logged
    existing_texts = {a.get("angle_text", "") for a in data.get("angles", [])}
    if angle_text in existing_texts:
        logger.info(
            "FC angle already logged (dedup) [%s]: %s",
            angle_type,
            angle_text[:80],
        )
        return

    entry = {
        "angle_text": angle_text,
        "blog_title": blog_title,
        "type": angle_type,
        "date": datetime.utcnow().isoformat(),
    }
    data.setdefault("angles", []).append(entry)
    _save_angles(data)

    logger.info(
        "Logged FC angle [%s] for '%s': %s",
        angle_type,
        blog_title,
        angle_text[:80],
    )


def _infer_template_type(topic: str) -> str:
    """Infer the best template type for a topic using keyword matching."""
    topic_lower = topic.lower()

    best_type = "other"
    best_score = 0

    for angle_type, keywords in TOPIC_TYPE_MAP.items():
        score = sum(1 for kw in keywords if kw in topic_lower)
        if score > best_score:
            best_score = score
            best_type = angle_type

    return best_type if best_score > 0 else "convenience"  # Default fallback


def _fill_template_variables(
    template: dict,
    topic: str,
    sub_context: str,
    api_key: str,
) -> str:
    """Fill template variables using a lightweight LLM call.

    Args:
        template: The template dict with "template" and "variables" fields.
        topic: The blog topic/title.
        sub_context: The sub-context being used.
        api_key: Anthropic API key.

    Returns the completed angle text.
    """
    variables = template.get("variables", [])
    if not variables:
        return template["template"]

    example_fills = template.get("example_fills", {})
    examples_str = ", ".join(
        f'{k}="{v}"' for k, v in example_fills.items()
    )

    prompt = (
        f"Fill in the variables for this FC mention template.\n\n"
        f"Template: {template['template']}\n"
        f"Variables to fill: {', '.join(variables)}\n"
        f"Example fills: {examples_str}\n\n"
        f"Blog topic: {topic}\n"
        f"Blog context: {sub_context}\n\n"
        f"Return ONLY a JSON object with the variable names as keys and "
        f"filled values as strings. No explanation."
    )

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            temperature=0.5,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Parse JSON response
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        fills = json.loads(raw.strip())

        # Apply fills to template
        result = template["template"]
        for var in variables:
            placeholder = "{" + var + "}"
            fill_value = fills.get(var, example_fills.get(var, var))
            result = result.replace(placeholder, str(fill_value))

        return result

    except Exception as e:
        logger.warning(
            "Failed to fill template variables via LLM: %s — using example fills.",
            e,
        )
        # Fall back to example fills
        result = template["template"]
        for var in variables:
            placeholder = "{" + var + "}"
            fill_value = example_fills.get(var, var)
            result = result.replace(placeholder, str(fill_value))
        return result


def _get_last_used_type(data: dict) -> Optional[str]:
    """Get the type of the most recently used angle."""
    angles = data.get("angles", [])
    if not angles:
        return None
    return angles[-1].get("type")


def auto_select_angle(
    topic: str,
    sub_context: str = "",
    api_key: Optional[str] = None,
) -> dict:
    """Auto-select an FC angle from the template library (Phase 2 only).

    Args:
        topic: The blog topic/title.
        sub_context: The sub-context being used for this blog.
        api_key: Anthropic API key for variable filling.

    Returns a dict with:
        angle_text: The completed angle instruction.
        template_type: The template type used.
        auto_selected: True (for distinguishing from manual angles).
        requires_review: True (pipeline should pause for review).
    """
    if api_key is None:
        api_key = os.environ.get("LLM_RELAY_SECRET_ANTHROPIC_API_KEY", "")

    data = _load_angles()
    templates = data.get("templates", [])

    if not templates:
        logger.warning("No templates available for auto-selection.")
        return {
            "angle_text": "",
            "template_type": "none",
            "auto_selected": True,
            "requires_review": True,
            "error": "No templates available.",
        }

    # Infer best type for this topic
    inferred_type = _infer_template_type(topic)

    # Avoid using the same type as the last blog
    last_type = _get_last_used_type(data)
    if inferred_type == last_type:
        # Pick a different type — prefer the next best match
        for angle_type in TOPIC_TYPE_MAP:
            if angle_type != last_type:
                topic_lower = topic.lower()
                score = sum(1 for kw in TOPIC_TYPE_MAP[angle_type] if kw in topic_lower)
                if score > 0:
                    inferred_type = angle_type
                    break
        # If still the same, just pick the least-used template type
        if inferred_type == last_type:
            type_usage = {}
            for t in templates:
                type_usage[t["type"]] = t.get("times_used", 0)
            sorted_types = sorted(type_usage.items(), key=lambda x: x[1])
            for t_type, _ in sorted_types:
                if t_type != last_type:
                    inferred_type = t_type
                    break

    # Find matching template
    selected_template = None
    for t in templates:
        if t["type"] == inferred_type:
            selected_template = t
            break

    if selected_template is None:
        # Fallback: use least-used template
        templates_sorted = sorted(templates, key=lambda t: t.get("times_used", 0))
        selected_template = templates_sorted[0]
        inferred_type = selected_template["type"]

    # Fill variables
    angle_text = _fill_template_variables(
        selected_template, topic, sub_context, api_key
    )

    # Update usage tracking
    selected_template["times_used"] = selected_template.get("times_used", 0) + 1
    selected_template["last_used"] = datetime.utcnow().isoformat()
    _save_angles(data)

    logger.info(
        "Auto-selected FC angle [%s]: %s",
        inferred_type,
        angle_text[:80],
    )

    return {
        "angle_text": angle_text,
        "template_type": inferred_type,
        "auto_selected": True,
        "requires_review": True,
    }


def get_angle(
    topic: str,
    manual_angle: Optional[str] = None,
    manual_angle_type: str = "other",
    sub_context: str = "",
    api_key: Optional[str] = None,
) -> dict:
    """Main entry point: get the FC integration angle for a blog.

    In Phase 1: requires manual_angle. If not provided, returns an error
    indicating the pipeline should halt and prompt for input.

    In Phase 2: auto-selects if no manual_angle provided. Manual always
    overrides auto-selection.

    Args:
        topic: The blog topic/title.
        manual_angle: Optional manual angle text (always takes priority).
        manual_angle_type: Type tag for manual angle.
        sub_context: The sub-context for this blog.
        api_key: Anthropic API key.

    Returns a dict with angle_text and metadata.
    """
    phase = get_phase()

    # Manual angle always takes priority
    if manual_angle:
        log_angle(manual_angle, topic, manual_angle_type)
        return {
            "angle_text": manual_angle,
            "template_type": manual_angle_type,
            "auto_selected": False,
            "requires_review": False,
            "phase": phase,
        }

    # Phase 1: require manual input
    if phase == 1:
        data = _load_angles()
        total = len(data.get("angles", []))
        logger.warning(
            "Phase 1 active (%d/%d angles logged) — manual FC angle required.",
            total,
            PHASE_2_MIN_ANGLES,
        )
        return {
            "angle_text": "",
            "template_type": "none",
            "auto_selected": False,
            "requires_review": False,
            "phase": 1,
            "error": (
                f"Phase 1 active ({total}/{PHASE_2_MIN_ANGLES} angles logged). "
                f"Manual fc_angle input required. Provide an fc_angle parameter."
            ),
        }

    # Phase 2: auto-select
    return auto_select_angle(topic, sub_context, api_key)


def list_angles() -> list[dict]:
    """Return all logged angles."""
    data = _load_angles()
    return data.get("angles", [])


def list_templates() -> list[dict]:
    """Return all templates (Phase 2)."""
    data = _load_angles()
    return data.get("templates", [])


def add_template(
    angle_type: str,
    template_text: str,
    variables: list[str],
    example_fills: dict[str, str],
) -> None:
    """Add a custom template to the library.

    Args:
        angle_type: One of VALID_TYPES.
        template_text: Template string with {variable} placeholders.
        variables: List of variable names used in the template.
        example_fills: Example values for each variable.
    """
    if angle_type not in VALID_TYPES:
        logger.warning("Invalid type '%s' — using 'other'.", angle_type)
        angle_type = "other"

    data = _load_angles()
    template = {
        "type": angle_type,
        "template": template_text,
        "variables": variables,
        "example_fills": example_fills,
        "times_used": 0,
        "last_used": None,
    }
    data.setdefault("templates", []).append(template)
    _save_angles(data)
    logger.info("Added FC angle template [%s].", angle_type)


def status() -> dict:
    """Return current FC angle manager status."""
    data = _load_angles()
    angles = data.get("angles", [])
    type_counts = _count_type_tags(angles)

    return {
        "phase": get_phase(),
        "total_angles": len(angles),
        "type_distribution": type_counts,
        "phase_2_eligible": is_phase_2_eligible()["eligible"],
        "phase_2_confirmed": data.get("phase_2_confirmed", False),
        "templates_count": len(data.get("templates", [])),
    }
