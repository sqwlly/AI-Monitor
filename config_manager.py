#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration Manager
Centralized configuration management with sensitive data masking
"""

import os
import re
from typing import Any, Dict, Optional


class ConfigManager:
    """
    Unified configuration manager

    Features:
    1. Centralized config access
    2. Sensitive data masking for logs
    3. Config validation
    4. Default value handling
    """

    # Sensitive key patterns
    SENSITIVE_PATTERNS = [
        "api_key", "apikey", "api-key",
        "token", "secret", "password",
        "credential", "auth"
    ]

    def __init__(self):
        """Initialize configuration manager"""
        self._config_cache: Dict[str, Any] = {}
        self._env_prefixes = ["AI_MONITOR_", "OPENAI_", "DASHSCOPE_"]

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value

        Search order:
        1. Cache
        2. Environment variables (with prefixes)
        3. Default value

        Args:
            key: Configuration key (will be uppercased for env lookup)
            default: Default value if not found

        Returns:
            Configuration value
        """
        # Check cache first
        if key in self._config_cache:
            return self._config_cache[key]

        # Try environment variables with different prefixes
        env_key = key.upper()
        for prefix in self._env_prefixes:
            env_value = os.environ.get(prefix + env_key)
            if env_value:
                self._config_cache[key] = env_value
                return env_value

        # Try exact key
        value = os.environ.get(env_key, default)
        self._config_cache[key] = value
        return value

    def set(self, key: str, value: Any) -> None:
        """
        Set configuration value (in-memory only)

        Args:
            key: Configuration key
            value: Value to set
        """
        self._config_cache[key] = value

    def get_masked(self, key: str, visible_chars: int = 4) -> str:
        """
        Get masked configuration value for logging

        Args:
            key: Configuration key
            visible_chars: Number of characters to show at start/end

        Returns:
            Masked value (e.g., "sk-1234...abcd")
        """
        value = self.get(key)
        if not value:
            return ""

        value_str = str(value)

        # Check if key is sensitive
        if self._is_sensitive_key(key):
            # Mask the value
            if len(value_str) <= visible_chars * 2:
                return "*" * len(value_str)
            return f"{value_str[:visible_chars]}...{value_str[-visible_chars:]}"

        return value_str

    def _is_sensitive_key(self, key: str) -> bool:
        """Check if key contains sensitive pattern"""
        key_lower = key.lower()
        return any(pattern in key_lower for pattern in self.SENSITIVE_PATTERNS)

    def get_llm_config(self) -> Dict[str, Any]:
        """
        Get LLM configuration (centralized access point)

        Returns:
            Dictionary with api_key, base_url, model, etc.
        """
        return {
            "api_key": self.get("LLM_API_KEY", ""),
            "base_url": self.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            "model": self.get("LLM_MODEL", "gpt-4o-mini"),
            "timeout": int(self.get("LLM_TIMEOUT", "20")),
            "max_tokens": int(self.get("LLM_MAX_TOKENS", "80")),
            "temperature": float(self.get("LLM_TEMPERATURE", "0.7")),
            "role": self.get("LLM_ROLE", "monitor"),
        }

    def get_llm_config_masked(self) -> Dict[str, str]:
        """
        Get LLM configuration with sensitive data masked

        Returns:
            Dictionary with masked values for logging
        """
        config = self.get_llm_config()
        return {
            "api_key": self._mask_value(config["api_key"]),
            "base_url": config["base_url"],
            "model": config["model"],
            "timeout": str(config["timeout"]),
            "max_tokens": str(config["max_tokens"]),
            "temperature": str(config["temperature"]),
            "role": config["role"],
        }

    def _mask_value(self, value: str, visible_chars: int = 4) -> str:
        """Mask a sensitive value"""
        if not value or len(value) <= visible_chars * 2:
            return "***"
        return f"{value[:visible_chars]}...{value[-visible_chars:]}"


# Global singleton instance
_global_config: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """Get global configuration manager instance"""
    global _global_config
    if _global_config is None:
        _global_config = ConfigManager()
    return _global_config


def reset_config() -> None:
    """Reset global configuration (mainly for testing)"""
    global _global_config
    _global_config = None
