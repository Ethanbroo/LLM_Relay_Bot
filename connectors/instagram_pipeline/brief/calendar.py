"""Content calendar for Instagram posting schedule.

Generates InstagramContentBriefs based on:
- Content pillar weights (personal_moment > lifestyle > fashion...)
- Day-of-week format rotation (feed posts vs. reels vs. carousels)
- Narrative seed banks (hardcoded story hooks per pillar)
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from .models import InstagramContentBrief, ContentFormat, ContentFormatWeights


# Content pillars and their posting weights.
# Higher weight = more frequent. Tune these to match desired feed ratio.
CONTENT_PILLAR_WEIGHTS = {
    "personal_moment": 0.30,   # Highest — most engagement, most authentic
    "lifestyle": 0.25,
    "fashion": 0.20,
    "wellness": 0.10,
    "food": 0.10,
    "travel": 0.05,
}

# Hard-coded narrative ideas per pillar. The LLM elaborates on these.
# These are Vice-style hooks — specific moments, not generic topics.
# Tuned for Solana's warm Mediterranean lifestyle aesthetic.
NARRATIVE_SEEDS = {
    "personal_moment": [
        "morning light hitting the terracotta tiles just right",
        "reading on the balcony when the breeze carries jasmine",
        "laughing at something on my phone mid-espresso",
        "that feeling when you finally finish something you were dreading",
        "the quiet after everyone leaves the cafe terrace",
        "golden hour hitting my face through the linen curtains",
        "when your playlist hits the exact right song at sunset",
        "watching the storm roll in from the rooftop",
        "catching my reflection in an old shop window and actually liking what I see",
        "the way afternoon light makes everything feel like a painting",
    ],
    "lifestyle": [
        "slow Sunday with nowhere to be and the whole coast ahead",
        "the kind of afternoon that makes you forget to check your phone",
        "when your outfit and your vibe actually match for once",
        "rearranging my corner of the apartment with market flowers",
        "perfect temperature day where the linen shirt is all you need",
        "finding the right lighting for absolutely everything",
        "morning routine but the window is open and everything smells like salt air",
        "that first outdoor coffee of the season",
    ],
    "fashion": [
        "first time wearing the thrifted linen find I've been saving",
        "outfit that technically breaks three of my own style rules",
        "the gold chain doing all the talking today",
        "when the wardrobe finally makes sense — all earth tones, all texture",
        "wearing something that makes you walk differently through the market",
        "that piece I almost didn't buy at the vintage shop",
        "the way this fabric catches the light is everything",
    ],
    "wellness": [
        "post-walk glow along the coast, no filter needed",
        "stretching in the morning sunlight on the terrace",
        "when your body tells you something your mind ignores",
        "actually drinking enough water for once, the Mediterranean way",
        "sleeping in and not feeling guilty about it",
        "that feeling after swimming in the sea before anyone else is awake",
        "yoga on the rooftop at golden hour",
    ],
    "food": [
        "made this twice this week and not sorry",
        "the breakfast that required zero effort — bread, oil, tomato, salt",
        "market haul becoming an actual meal on the terrace",
        "eating dinner standing up because it's that good",
        "when the cortado is better than expected",
        "leftovers that somehow taste better the next day with fresh herbs",
        "the perfect peach at the perfect moment",
    ],
    "travel": [
        "this corner of the old town that nobody talks about",
        "third time in this coastal town and still finding new streets",
        "what it actually looks like on a Tuesday vs. Instagram",
        "place I walked past a hundred times before noticing the tiles",
        "locals-only spot where the wine is cheaper than water",
        "that alley where the bougainvillea meets the stone wall",
    ],
}

# Format schedule: rotates by day of week
# Monday/Wednesday/Friday = feed posts; Tuesday/Thursday = reels; Weekend = carousel
FORMAT_SCHEDULE = {
    0: "single_image",   # Monday
    1: "reel",           # Tuesday
    2: "single_image",   # Wednesday
    3: "reel",           # Thursday
    4: "single_image",   # Friday
    5: "carousel",       # Saturday
    6: "carousel",       # Sunday
}

# Tone mappings per pillar
TONE_MAP = {
    "personal_moment": "intimate",
    "lifestyle": "aspirational",
    "fashion": "playful",
    "wellness": "reflective",
    "food": "relatable_commentary",
    "travel": "aspirational",
}

# Emotion mappings per pillar
EMOTION_MAP = {
    "personal_moment": "warmth and gentle relatability",
    "lifestyle": "quiet envy mixed with inspiration",
    "fashion": "fun and self-expression",
    "wellness": "introspective calm",
    "food": "hunger and comfort",
    "travel": "wanderlust and discovery",
}

# Caption style rotation (weekly cycle for variety)
CAPTION_STYLES = [
    "short_punchy",
    "storytelling",
    "question",
    "minimal",
    "relatable_commentary"
]


class ContentCalendar:
    """
    Generates a queue of InstagramContentBriefs for the week.

    Uses weighted random selection for content pillars and
    deterministic format rotation by day of week.
    """

    def __init__(self, character_id: str, seed: Optional[int] = None):
        """
        Initialize content calendar.

        Args:
            character_id: Character ID to use for all briefs
            seed: Optional random seed for reproducibility
        """
        self.character_id = character_id
        if seed is not None:
            random.seed(seed)

    def generate_brief(self, post_date: datetime) -> InstagramContentBrief:
        """
        Generate a single content brief for a given date.

        The scheduler calls this; the LLM expands it in Stage 2.

        Args:
            post_date: Date/time for scheduled post

        Returns:
            InstagramContentBrief ready for LLM expansion
        """
        # Select content pillar using weighted random
        pillar = random.choices(
            list(CONTENT_PILLAR_WEIGHTS.keys()),
            weights=list(CONTENT_PILLAR_WEIGHTS.values()),
        )[0]

        # Select narrative seed from pillar
        seed = random.choice(NARRATIVE_SEEDS[pillar])

        # Format determined by day of week
        post_format = FORMAT_SCHEDULE[post_date.weekday()]

        # Derive tone and emotion from pillar
        tone = TONE_MAP.get(pillar, "intimate")
        emotion = EMOTION_MAP.get(pillar, "connection")

        # Caption style rotates through the week
        caption_style = CAPTION_STYLES[post_date.weekday() % len(CAPTION_STYLES)]

        # Platform target based on format
        platform_target = (
            "instagram_reels" if post_format == "reel"
            else "instagram_feed"
        )

        # Select content format (video generation path)
        content_format = self._select_format(post_date)

        # Override post_format and platform_target based on content_format
        if content_format != ContentFormat.STATIC_IMAGE:
            post_format = "reel"
            platform_target = "instagram_reels"

        brief = InstagramContentBrief(
            character_id=self.character_id,
            content_pillar=pillar,
            post_format=post_format,
            content_format=content_format,
            narrative_hook=seed,
            scene_description="",          # Filled in by intent_builder.py in Stage 2
            tone=tone,
            target_emotion=emotion,
            caption_style=caption_style,
            hashtag_strategy="mixed",      # Can be made pillar-specific if needed
            platform_target=platform_target,
            scheduled_post_time=post_date.isoformat(),
        )

        return brief.with_hash()

    def _select_format(self, post_date: datetime) -> ContentFormat:
        """Select content format using weighted random with day-of-week adjustment."""
        weights = ContentFormatWeights()
        day = post_date.weekday()

        # Day-of-week adjustment
        if day in (0, 2, 4):  # Mon/Wed/Fri — reel-heavy days
            weights.avatar_talking = 0.40
            weights.static_image = 0.15
        elif day in (5, 6):    # Sat/Sun — casual content
            weights.gameplay_overlay = 0.20
            weights.narrative_reel = 0.10

        normalized = weights.normalized()

        formats = list(ContentFormat)
        format_weights = [normalized.get(f.value, 0.0) for f in formats]

        return random.choices(formats, weights=format_weights, k=1)[0]

    def generate_week(self, start_date: Optional[datetime] = None) -> list[InstagramContentBrief]:
        """
        Generate a full week of content briefs.

        Defaults to 3 posts per week (Mon, Wed, Fri) following Instagram best practices.

        Args:
            start_date: Week start date (defaults to next Monday)

        Returns:
            List of InstagramContentBriefs for the week
        """
        if start_date is None:
            # Start from next Monday
            today = datetime.now()
            days_until_monday = (7 - today.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7  # If today is Monday, start next week
            start_date = today + timedelta(days=days_until_monday)

        # Generate briefs for Monday, Wednesday, Friday (3x/week)
        briefs = []
        posting_days = [0, 2, 4]  # Mon, Wed, Fri

        for day_offset in posting_days:
            post_date = start_date + timedelta(days=day_offset)
            # Set time to 12:00 PM (noon) — optimal Instagram posting time
            post_date = post_date.replace(hour=12, minute=0, second=0, microsecond=0)
            brief = self.generate_brief(post_date)
            briefs.append(brief)

        return briefs

    def generate_month(self, year: int, month: int) -> list[InstagramContentBrief]:
        """
        Generate a full month of content briefs.

        Args:
            year: Year (e.g., 2026)
            month: Month (1-12)

        Returns:
            List of InstagramContentBriefs for the month (12-13 posts)
        """
        # Start from first Monday of the month
        first_day = datetime(year, month, 1, 12, 0)  # 12:00 PM
        days_until_monday = (7 - first_day.weekday()) % 7
        start_date = first_day + timedelta(days=days_until_monday)

        briefs = []
        posting_days = [0, 2, 4]  # Mon, Wed, Fri

        current_date = start_date
        while current_date.month == month:
            for day_offset in posting_days:
                post_date = current_date + timedelta(days=day_offset)
                if post_date.month == month:
                    brief = self.generate_brief(post_date)
                    briefs.append(brief)

            # Move to next week
            current_date += timedelta(days=7)

        return briefs

    def get_pillar_distribution(self, briefs: list[InstagramContentBrief]) -> dict[str, int]:
        """
        Analyze pillar distribution in a set of briefs.

        Useful for verifying weighted random is working as expected.

        Args:
            briefs: List of content briefs

        Returns:
            Dict mapping pillar name to count
        """
        distribution = {}
        for brief in briefs:
            pillar = brief.content_pillar
            distribution[pillar] = distribution.get(pillar, 0) + 1
        return distribution

    def get_format_distribution(self, briefs: list[InstagramContentBrief]) -> dict[str, int]:
        """
        Analyze format distribution in a set of briefs.

        Args:
            briefs: List of content briefs

        Returns:
            Dict mapping format to count
        """
        distribution = {}
        for brief in briefs:
            fmt = brief.post_format
            distribution[fmt] = distribution.get(fmt, 0) + 1
        return distribution
