"""Shared external-notification switch, matching the pipeline shell scripts."""
import os


def notifications_enabled() -> bool:
    # Fail closed; enabling requires the same explicit value as the pipelines.
    return os.getenv("ENABLE_NOTIFICATIONS", "0") == "1"
