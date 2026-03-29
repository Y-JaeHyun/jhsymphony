from __future__ import annotations

import logging
from typing import Any

from jhsymphony.config import RoutingRule

logger = logging.getLogger(__name__)


class ProviderRouter:
    def __init__(
        self,
        default_provider: str,
        providers: dict[str, Any],
        routing_rules: list[RoutingRule],
    ) -> None:
        self._default = default_provider
        self._providers = providers
        self._rules = routing_rules

    def select(self, labels: list[str]) -> Any:
        for rule in self._rules:
            if rule.label in labels:
                if rule.provider in self._providers:
                    return self._providers[rule.provider]
                logger.warning(
                    "Routing rule matched label '%s' -> provider '%s' but provider not found, using default",
                    rule.label, rule.provider,
                )
        return self._providers[self._default]

    def get(self, name: str) -> Any | None:
        return self._providers.get(name)
