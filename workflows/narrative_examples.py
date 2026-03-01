"""Narrative reference examples for blog generation.

Lightweight reference bank of article structures and voice patterns.
Inspired by Vice, The Outline, Byrdie (personal storytelling mode).

NO web search needed — these are hardcoded reference patterns to guide
narrative voice, not content to copy.
"""

# Vice-style opening hooks (story first, thesis later)
NARRATIVE_HOOKS = [
    "Sarah's therapist asked her to name three friends. She stared at the ceiling for 47 seconds before changing the subject.",
    "The last time Mike had a genuine conversation was three weeks ago. He remembers because it made him uncomfortable.",
    "Nobody warned Emma that making friends after 30 would feel like dating, except more awkward and with worse apps.",
    "Jamie's calendar has 18 Zoom meetings this week. Zero coffee dates. The math isn't working.",
    "When Alex moved to the city, they assumed loneliness was temporary. That was four years ago.",
]

# Story-driven structure (narrative arc, not listicle)
NARRATIVE_STRUCTURES = {
    "opening_tension": [
        "Start with a specific moment, not a statistic",
        "Use concrete details (time, place, feeling)",
        "Introduce the problem through a person, not a concept",
    ],
    "tension_escalation": [
        "Show the cost (what's at stake)",
        "Use internal monologue or dialogue",
        "Avoid 'many people feel this way' — stick to the story",
    ],
    "insight_turn": [
        "Introduce the psychological mechanism (HOW, not just WHAT)",
        "Connect the personal to the structural",
        "Use metaphor or comparison (not clinical terminology)",
    ],
    "resolution_gesture": [
        "Offer a path forward (not a prescription)",
        "Keep it actionable but not bossy",
        "End with a question or observation, not a command",
    ],
}

# Voice patterns (what to avoid, what to embrace)
VOICE_GUIDELINES = {
    "avoid": [
        "You should...",
        "It's important to...",
        "Studies show...",
        "Research indicates...",
        "The key is...",
        "Simply...",
        "Just...",
        "Easy steps to...",
    ],
    "embrace": [
        "Here's what happened:",
        "Nobody talks about...",
        "The part they don't tell you:",
        "It turns out...",
        "What actually works:",
        "The quiet part:",
        "This is where it gets weird:",
    ],
}

# Sarcasm/humor calibration (dry, not mean)
SARCASM_EXAMPLES = {
    "dry_observation": [
        "Therapists call it 'social atrophy.' Your bank account calls it 'Friday night savings.'",
        "LinkedIn says you have 500+ connections. Your phone says you have zero dinner plans.",
        "The algorithm thinks you love inspirational quotes about friendship. You're just lonely.",
    ],
    "tension_relief": [
        "Turns out, 'putting yourself out there' is not a personality trait.",
        "The advice is always 'be vulnerable.' Cool, with who?",
        "Everyone says community is important. Nobody says where to find one that isn't a cult.",
    ],
}

# Structural examples (how articles flow)
ARTICLE_FLOW_TEMPLATE = """
NARRATIVE FLOW:
1. Hook: Specific moment (person, place, feeling)
2. Problem: Why this moment matters (structural, not individual failure)
3. Mechanism: Psychological/social explanation (HOW it works)
4. Insight: The uncomfortable truth nobody says
5. Path: Actionable gesture (not prescription)
6. Close: Question or observation (not command)

AVOID:
- Opening with statistics
- Generic "many people struggle"
- Listicle format ("5 ways to...")
- Closing with "reach out" or "you're not alone"

EMBRACE:
- Specific scenes
- Internal monologue
- Structural critique (work culture, app design, modern isolation)
- Dark humor that acknowledges pain (not toxic positivity)
"""

def get_narrative_guidance() -> str:
    """Return narrative guidance for blog generation prompt."""
    return f"""
NARRATIVE VOICE REFERENCE:

Opening Hook Examples (story first, not statistics):
- {NARRATIVE_HOOKS[0]}
- {NARRATIVE_HOOKS[1]}
- {NARRATIVE_HOOKS[2]}

Voice Patterns to AVOID:
- "You should..." (prescriptive)
- "Studies show..." (boring academic tone)
- "It's important to..." (preachy)
- "Simply..." or "Just..." (dismissive platitudes)

Voice Patterns to EMBRACE:
- "Here's what happened:" (storytelling)
- "Nobody talks about..." (insider knowledge)
- "The part they don't tell you:" (structural critique)
- "Turns out..." (discovery narrative)

Sarcasm Calibration (dry, not mean):
- "Therapists call it 'social atrophy.' Your bank account calls it 'Friday night savings.'"
- "LinkedIn says you have 500+ connections. Your phone says you have zero dinner plans."

{ARTICLE_FLOW_TEMPLATE}
"""
