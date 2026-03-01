"""Base template class.

All templates inherit from BaseTemplate. A template is a class that takes
structured content inputs and produces a Timeline.

Templates are:
- Declarative: user provides content, template defines structure
- Validated: inputs are checked before timeline generation
- Composable with effects: templates can include effect chains per clip
"""

from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel, Field

from ..schemas import Timeline, AspectRatio


class TemplateInput(BaseModel):
    """Base input for all templates.

    Each template extends this with its own fields. Common fields
    are defined here so every template has a consistent interface.
    """
    title: Optional[str] = None
    character_ids: list[str] = Field(default_factory=list)
    aspect_ratio: AspectRatio = AspectRatio.PORTRAIT_9_16
    duration_seconds: Optional[int] = Field(
        default=None,
        description="Override template default duration. None = template decides."
    )


class BaseTemplate(ABC):
    """Base class for video templates.

    Subclasses must implement build_timeline() and set class-level metadata.

    Usage:
        template = InstagramReelSlideshow()
        errors = template.validate_inputs(inputs)
        if not errors:
            timeline = template.build_timeline(inputs)
    """

    # Metadata — override in subclasses
    name: str = "base"
    description: str = ""
    supported_platforms: list[str] = []
    default_duration_seconds: int = 15
    min_images: int = 1
    max_images: int = 20

    @abstractmethod
    def build_timeline(self, inputs: TemplateInput) -> Timeline:
        """Convert template inputs into a renderable Timeline.

        This is the core method. Each template implements its own
        visual logic here.

        Args:
            inputs: Template-specific input model

        Returns:
            Complete Timeline ready for rendering
        """
        ...

    def validate_inputs(self, inputs: TemplateInput) -> list[str]:
        """Optional input validation.

        Returns:
            List of error messages. Empty list = valid.
        """
        return []

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
