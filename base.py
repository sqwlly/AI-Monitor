#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Base Classes
Provides common base classes for the application
"""

from typing import Any, Dict


class SerializableMixin:
    """
    Mixin class providing automatic serialization

    Automatically converts all instance attributes to dictionary.
    Subclasses can override to_dict() for custom behavior.
    """

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert object to dictionary

        Returns:
            Dictionary representation of the object
        """
        # Get all non-private, non-method attributes
        result = {}
        for key in dir(self):
            # Skip private attributes and methods
            if key.startswith('_'):
                continue
            value = getattr(self, key)
            # Skip methods and properties that are methods
            if callable(value) and not isinstance(value, (str, list, dict)):
                continue
            # Skip class attributes
            if key in dir(self.__class__):
                continue
            result[key] = value
        return result


class DataClassMixin(SerializableMixin):
    """
    Enhanced mixin for dataclass-like objects

    Provides to_dict() that includes all fields defined in __init__.
    Tracks the original __init__ signature for accurate serialization.
    """

    _fields: set = None

    def __init_subclass__(cls, **kwargs):
        """Track fields from __init__ signature"""
        super().__init_subclass__(**kwargs)
        # Try to get __init__ annotations
        if hasattr(cls.__init__, '__annotations__'):
            cls._fields = set(cls.__init__.__annotations__.keys())

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary using tracked fields

        Returns:
            Dictionary with all defined fields
        """
        if self._fields:
            return {k: getattr(self, k, None) for k in self._fields}
        # Fallback to parent implementation
        return super().to_dict()


__all__ = ['SerializableMixin', 'DataClassMixin']
