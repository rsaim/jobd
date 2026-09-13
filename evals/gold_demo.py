"""Re-export: the gold labels live in the package now (`jobd.services.demo_gold`),
so the demo replay extractor and this eval score against the same list."""

from jobd.services.demo_gold import GOLD, Gold

__all__ = ["GOLD", "Gold"]
