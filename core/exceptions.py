#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自定义异常类
定义项目中的所有异常类型
"""


class MonitorError(Exception):
    """监控器基础异常"""
    pass


class ConfigError(MonitorError):
    """配置错误"""
    pass


class ToolError(MonitorError):
    """工具执行错误"""
    pass


class DatabaseError(MonitorError):
    """数据库操作错误"""
    pass


class ValidationError(MonitorError):
    """输入验证错误"""
    pass


class SecurityError(MonitorError):
    """安全相关错误"""
    pass
