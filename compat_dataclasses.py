# -*- coding: utf-8 -*-
"""
dataclasses 兼容层（Python 3.6 友好）

本项目部分模块使用 @dataclass / field(default_factory=...) 来减少样板代码。
但运行环境可能是 Python 3.6（无标准库 dataclasses），因此提供最小兼容实现：

- 支持 `@dataclass`（无参数）
- 支持 `field(default=...)` 与 `field(default_factory=...)`
- 生成 `__init__`，支持位置参数与关键字参数
- 若存在 `__post_init__`，在初始化末尾调用

不实现 repr/eq/order/frozen 等高级特性（当前代码未使用）。
"""

try:
    from dataclasses import dataclass, field  # type: ignore
except Exception:  # pragma: no cover
    _MISSING = object()

    class _Field:
        __slots__ = ("default", "default_factory")

        def __init__(self, default=_MISSING, default_factory=_MISSING):
            self.default = default
            self.default_factory = default_factory

    def field(*, default=_MISSING, default_factory=_MISSING):  # noqa: A001
        if default is not _MISSING and default_factory is not _MISSING:
            raise ValueError("field() cannot specify both default and default_factory")
        return _Field(default=default, default_factory=default_factory)

    def dataclass(cls):  # noqa: D401
        annotations = getattr(cls, "__annotations__", {}) or {}
        field_names = list(annotations.keys())

        specs = {}
        for name in field_names:
            value = getattr(cls, name, _MISSING)
            if isinstance(value, _Field):
                specs[name] = value
                if value.default is not _MISSING:
                    setattr(cls, name, value.default)
                else:
                    try:
                        delattr(cls, name)
                    except Exception:
                        pass
            else:
                specs[name] = _Field(default=value, default_factory=_MISSING) if value is not _MISSING else _Field()

        def __init__(self, *args, **kwargs):
            if len(args) > len(field_names):
                raise TypeError("Too many positional arguments")

            for name, val in zip(field_names, args):
                setattr(self, name, val)

            for name in field_names[len(args):]:
                if name in kwargs:
                    setattr(self, name, kwargs.pop(name))
                    continue

                spec = specs.get(name) or _Field()
                if spec.default_factory is not _MISSING:
                    setattr(self, name, spec.default_factory())
                elif spec.default is not _MISSING:
                    setattr(self, name, spec.default)
                else:
                    raise TypeError("Missing required argument: {}".format(name))

            if kwargs:
                unexpected = ", ".join(sorted(kwargs.keys()))
                raise TypeError("Unexpected arguments: {}".format(unexpected))

            post_init = getattr(self, "__post_init__", None)
            if callable(post_init):
                post_init()

        cls.__init__ = __init__
        cls.__dataclass_fields__ = specs
        return cls

