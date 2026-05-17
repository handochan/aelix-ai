"""Built-in extensions shipped with Aelix (ADR-0004).

Both :class:`PolicyExtension` and :class:`GuardrailExtension` are ordinary
extensions: they ride the same hook bus + loader path as third-party
extensions. They are imported here for convenience.
"""

from aelix.builtin.guardrail import (
    DEFAULT_GUARDRAIL_RULES,
    GuardrailExtension,
    GuardrailRule,
)
from aelix.builtin.policy import PolicyExtension

__all__ = [
    "DEFAULT_GUARDRAIL_RULES",
    "GuardrailExtension",
    "GuardrailRule",
    "PolicyExtension",
]
