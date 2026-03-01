"""Provider registry for managing multiple generation providers.

Allows fallback behavior when primary provider fails or is rate-limited.
"""

import logging
from typing import Optional

from .base import AbstractAssetGenerator, GenerationRequest, GenerationResult

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Manages multiple asset generation providers with automatic fallback.

    Example usage:
        registry = ProviderRegistry()
        registry.register("fal", fal_generator, primary=True)
        registry.register("replicate", replicate_generator, primary=False)

        result = registry.generate(request)  # Tries fal first, falls back to replicate
    """

    def __init__(self):
        self.providers: dict[str, AbstractAssetGenerator] = {}
        self.primary_provider: Optional[str] = None

    def register(
        self,
        name: str,
        provider: AbstractAssetGenerator,
        primary: bool = False
    ) -> None:
        """
        Register a new provider.

        Args:
            name: Provider identifier (e.g., "fal", "replicate")
            provider: Provider instance
            primary: If True, use this as the default provider
        """
        self.providers[name] = provider

        if primary or self.primary_provider is None:
            self.primary_provider = name

        logger.info(
            "Registered provider '%s'%s",
            name,
            " (primary)" if primary else ""
        )

    def generate(
        self,
        request: GenerationRequest,
        provider_name: Optional[str] = None,
        enable_fallback: bool = True
    ) -> GenerationResult:
        """
        Generate asset using specified provider or primary provider.

        Args:
            request: Generation parameters
            provider_name: Specific provider to use (None = use primary)
            enable_fallback: If True, try other providers on failure

        Returns:
            GenerationResult

        Raises:
            RuntimeError: If all providers fail
        """
        # Determine provider order
        if provider_name:
            providers_to_try = [provider_name]
            if enable_fallback:
                # Add other providers as fallbacks
                providers_to_try.extend(
                    [p for p in self.providers.keys() if p != provider_name]
                )
        else:
            # Use primary first, then others
            if not self.primary_provider:
                raise RuntimeError("No providers registered")

            providers_to_try = [self.primary_provider]
            if enable_fallback:
                providers_to_try.extend(
                    [p for p in self.providers.keys() if p != self.primary_provider]
                )

        # Try providers in order
        last_error = None
        for provider_name in providers_to_try:
            provider = self.providers.get(provider_name)
            if not provider:
                logger.warning("Provider '%s' not found, skipping", provider_name)
                continue

            try:
                logger.info("Attempting generation with provider '%s'", provider_name)
                result = provider.generate(request)
                logger.info("Generation successful with provider '%s'", provider_name)
                return result
            except Exception as e:
                logger.warning(
                    "Provider '%s' failed: %s%s",
                    provider_name,
                    e,
                    " (trying fallback)" if enable_fallback else ""
                )
                last_error = e
                continue

        # All providers failed
        raise RuntimeError(
            f"All providers failed. Last error: {last_error}"
        )

    def estimate_cost(
        self,
        request: GenerationRequest,
        provider_name: Optional[str] = None
    ) -> float:
        """
        Estimate cost for generation request.

        Args:
            request: Generation parameters
            provider_name: Specific provider (None = use primary)

        Returns:
            Estimated cost in USD
        """
        provider_name = provider_name or self.primary_provider
        if not provider_name:
            raise RuntimeError("No providers registered")

        provider = self.providers.get(provider_name)
        if not provider:
            raise ValueError(f"Provider '{provider_name}' not found")

        return provider.estimate_cost(request)

    def health_check_all(self) -> dict[str, bool]:
        """
        Run health check on all registered providers.

        Returns:
            Dict mapping provider name to health status
        """
        results = {}
        for name, provider in self.providers.items():
            try:
                is_healthy = provider.health_check()
                results[name] = is_healthy
                logger.info(
                    "Provider '%s' health check: %s",
                    name,
                    "PASS" if is_healthy else "FAIL"
                )
            except Exception as e:
                logger.error("Provider '%s' health check error: %s", name, e)
                results[name] = False

        return results

    def get_provider(self, name: str) -> Optional[AbstractAssetGenerator]:
        """Get provider by name."""
        return self.providers.get(name)

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return list(self.providers.keys())
