#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Notification Hub
人机协作通知系统 - 关键事件时主动通知人类

Usage:
    python3 notification_hub.py send <event_type> <title> <message> [--context JSON]
    python3 notification_hub.py test [channel]
    python3 notification_hub.py config show
    python3 notification_hub.py config set <key> <value>

Event Types:
    dangerous_operation  - 检测到危险命令被阻止
    stuck               - 连续N分钟无进展
    critical_error      - 死循环或严重错误
    human_needed        - 需要人工决策
    task_completed      - 任务完成（可选）
    long_running        - 运行超过阈值
    goal_drift          - 目标偏离
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

try:
    from typing import Optional, List, Dict, Any
except ImportError:
    pass

# 默认配置
DEFAULT_CONFIG_DIR = Path.home() / ".tmux-monitor" / "config"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "notification.json"

# 环境变量
CONFIG_PATH = Path(os.environ.get("AI_MONITOR_NOTIFICATION_CONFIG", str(DEFAULT_CONFIG_PATH)))
NOTIFICATION_ENABLED = os.environ.get("AI_MONITOR_NOTIFICATION_ENABLED", "0") == "1"

# 事件严重级别映射
EVENT_SEVERITY = {
    "dangerous_operation": "critical",
    "stuck": "warning",
    "critical_error": "error",
    "human_needed": "warning",
    "task_completed": "info",
    "long_running": "info",
    "goal_drift": "warning"
}


class NotificationEvent:
    """通知事件"""
    def __init__(self, event_type, title, message, severity=None, context=None):
        self.event_type = event_type
        self.title = title
        self.message = message
        self.severity = severity or EVENT_SEVERITY.get(event_type, "info")
        self.context = context or {}
        self.timestamp = int(time.time())


class DesktopNotifier:
    """桌面通知器 - Linux/macOS"""

    def __init__(self, config=None):
        self.config = config or {}

    def send(self, event):
        """发送桌面通知"""
        system = platform.system()
        title = event.title
        message = event.message
        urgency = self._map_urgency(event.severity)

        try:
            if system == 'Darwin':  # macOS
                self._send_macos(title, message)
            elif system == 'Linux':
                self._send_linux(title, message, urgency)
            else:
                # Windows 或其他系统
                print("[notification] Desktop notifications not supported on {}".format(system),
                      file=sys.stderr)
                return False
            return True
        except Exception as e:
            print("[notification] Error sending desktop notification: {}".format(e), file=sys.stderr)
            return False

    def _send_macos(self, title, message):
        """macOS 通知"""
        # 转义特殊字符
        title = title.replace('"', '\\"')
        message = message.replace('"', '\\"')

        script = 'display notification "{}" with title "{}"'.format(message, title)
        subprocess.run(['osascript', '-e', script], check=False,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _send_linux(self, title, message, urgency):
        """Linux 通知 (notify-send)"""
        # 检查 notify-send 是否可用
        result = subprocess.run(['which', 'notify-send'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            # 尝试 zenity
            result = subprocess.run(['which', 'zenity'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                subprocess.run([
                    'zenity', '--notification',
                    '--text={}: {}'.format(title, message)
                ], check=False)
                return

            print("[notification] notify-send not found, trying wall", file=sys.stderr)
            # 降级到 wall
            subprocess.run(['wall', '{}: {}'.format(title, message)],
                          check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return

        cmd = [
            'notify-send',
            '-u', urgency,
            '-a', 'Claude Monitor',
            '-i', self._get_icon(urgency),
            title,
            message
        ]
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _map_urgency(self, severity):
        """映射严重级别到 notify-send urgency"""
        mapping = {
            "info": "low",
            "warning": "normal",
            "error": "critical",
            "critical": "critical"
        }
        return mapping.get(severity, "normal")

    def _get_icon(self, urgency):
        """获取通知图标"""
        icons = {
            "low": "dialog-information",
            "normal": "dialog-warning",
            "critical": "dialog-error"
        }
        return icons.get(urgency, "dialog-information")

    def test(self):
        """测试通知"""
        event = NotificationEvent(
            event_type="test",
            title="Claude Monitor 测试",
            message="如果你看到这条通知，说明桌面通知功能正常工作！",
            severity="info"
        )
        return self.send(event)


class WebhookNotifier:
    """Webhook 通知器"""

    def __init__(self, config):
        self.url = config.get('url', '')
        self.method = config.get('method', 'POST')
        self.headers = config.get('headers', {})
        self.template = config.get('template')

    def send(self, event):
        """发送 Webhook 通知"""
        if not self.url:
            print("[notification] Webhook URL not configured", file=sys.stderr)
            return False

        try:
            import urllib.request
            import urllib.error

            payload = self._render_payload(event)
            data = json.dumps(payload).encode('utf-8')

            headers = {'Content-Type': 'application/json'}
            headers.update(self.headers)

            req = urllib.request.Request(self.url, data=data, headers=headers, method=self.method)

            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400

        except Exception as e:
            print("[notification] Webhook error: {}".format(e), file=sys.stderr)
            return False

    def _render_payload(self, event):
        """渲染 payload"""
        if self.template:
            # 简单模板替换
            payload_str = self.template
            payload_str = payload_str.replace("${event_type}", event.event_type)
            payload_str = payload_str.replace("${title}", event.title)
            payload_str = payload_str.replace("${message}", event.message)
            payload_str = payload_str.replace("${severity}", event.severity)
            payload_str = payload_str.replace("${timestamp}", str(event.timestamp))
            return json.loads(payload_str)

        return {
            "event_type": event.event_type,
            "title": event.title,
            "message": event.message,
            "severity": event.severity,
            "timestamp": event.timestamp,
            "context": event.context,
            "source": "claude-monitor"
        }

    def test(self):
        """测试 Webhook"""
        event = NotificationEvent(
            event_type="test",
            title="Claude Monitor Test",
            message="Webhook notification test",
            severity="info"
        )
        return self.send(event)


class NotificationHub:
    """通知中心"""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        self.notifiers = self._init_notifiers()
        self.last_notify_time = {}  # 防抖
        self.throttle_seconds = self.config.get('throttle_seconds', 60)

    def _load_config(self):
        """加载配置"""
        if self.config_path.exists():
            try:
                with open(str(self.config_path), 'r') as f:
                    return json.load(f)
            except Exception as e:
                print("[notification] Error loading config: {}".format(e), file=sys.stderr)

        # 默认配置
        return self._get_default_config()

    def _get_default_config(self):
        """获取默认配置"""
        return {
            "enabled": True,
            "throttle_seconds": 60,
            "quiet_hours": {
                "enabled": True,
                "start": "23:00",
                "end": "08:00"
            },
            "channels": [
                {
                    "type": "desktop",
                    "enabled": True,
                    "events": ["dangerous_operation", "stuck", "critical_error", "human_needed"]
                }
            ]
        }

    def _init_notifiers(self):
        """初始化通知器"""
        notifiers = {}

        for channel in self.config.get('channels', []):
            if not channel.get('enabled', False):
                continue

            channel_type = channel.get('type')
            events = channel.get('events', ['*'])

            if channel_type == 'desktop':
                notifiers[channel_type] = (DesktopNotifier(channel.get('config')), events)
            elif channel_type == 'webhook':
                notifiers[channel_type] = (WebhookNotifier(channel.get('config', {})), events)
            # 可扩展更多通知器

        return notifiers

    def notify(self, event):
        """发送通知，返回成功的渠道列表"""
        if not self.config.get('enabled', True):
            return []

        # 检查静默时段
        if self._is_quiet_hours():
            return []

        # 防抖检查
        key = "{}:{}".format(event.event_type, event.context.get('target', ''))
        if self._is_throttled(key):
            return []

        sent_channels = []
        for channel_type, (notifier, events) in self.notifiers.items():
            if '*' in events or event.event_type in events:
                if notifier.send(event):
                    sent_channels.append(channel_type)

        if sent_channels:
            self.last_notify_time[key] = time.time()

        return sent_channels

    def _is_quiet_hours(self):
        """检查是否在静默时段"""
        quiet = self.config.get('quiet_hours', {})
        if not quiet.get('enabled', False):
            return False

        try:
            now = time.localtime()
            current_minutes = now.tm_hour * 60 + now.tm_min

            start_parts = quiet.get('start', '23:00').split(':')
            start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])

            end_parts = quiet.get('end', '08:00').split(':')
            end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

            if start_minutes > end_minutes:
                # 跨午夜
                return current_minutes >= start_minutes or current_minutes < end_minutes
            else:
                return start_minutes <= current_minutes < end_minutes

        except Exception:
            return False

    def _is_throttled(self, key):
        """检查是否被限流"""
        last_time = self.last_notify_time.get(key, 0)
        return (time.time() - last_time) < self.throttle_seconds

    def test_all(self):
        """测试所有通知渠道"""
        results = {}
        for channel_type, (notifier, _) in self.notifiers.items():
            results[channel_type] = notifier.test()
        return results

    def save_config(self):
        """保存配置"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.config_path), 'w') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Notification Hub',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # send
    p_send = subparsers.add_parser('send', help='Send a notification')
    p_send.add_argument('event_type', help='Event type')
    p_send.add_argument('title', help='Notification title')
    p_send.add_argument('message', help='Notification message')
    p_send.add_argument('--context', help='JSON context', default='{}')
    p_send.add_argument('--force', action='store_true', help='Ignore throttle and quiet hours')

    # test
    p_test = subparsers.add_parser('test', help='Test notification channels')
    p_test.add_argument('channel', nargs='?', help='Channel to test (default: all)')

    # config
    p_config = subparsers.add_parser('config', help='Manage configuration')
    config_sub = p_config.add_subparsers(dest='config_cmd')
    config_sub.add_parser('show', help='Show current config')
    p_set = config_sub.add_parser('set', help='Set a config value')
    p_set.add_argument('key', help='Config key (e.g., enabled, throttle_seconds)')
    p_set.add_argument('value', help='Config value')
    config_sub.add_parser('init', help='Create default config file')

    args = parser.parse_args(argv)

    # 初始化
    hub = NotificationHub()

    try:
        if args.command == 'send':
            context = json.loads(args.context) if args.context else {}
            event = NotificationEvent(
                event_type=args.event_type,
                title=args.title,
                message=args.message,
                context=context
            )

            if args.force:
                hub.throttle_seconds = 0
                hub.config['quiet_hours']['enabled'] = False

            channels = hub.notify(event)
            if channels:
                print("Notification sent to: {}".format(', '.join(channels)))
            else:
                print("No notification sent (throttled, quiet hours, or no channels enabled)")

        elif args.command == 'test':
            if args.channel:
                if args.channel in hub.notifiers:
                    notifier, _ = hub.notifiers[args.channel]
                    success = notifier.test()
                    print("{}: {}".format(args.channel, "✅ OK" if success else "❌ Failed"))
                else:
                    print("Channel '{}' not found. Available: {}".format(
                        args.channel, ', '.join(hub.notifiers.keys())))
                    return 1
            else:
                results = hub.test_all()
                for channel, success in results.items():
                    print("{}: {}".format(channel, "✅ OK" if success else "❌ Failed"))

                if not results:
                    print("No channels configured. Run 'notification_hub.py config init' first.")

        elif args.command == 'config':
            if args.config_cmd == 'show':
                print(json.dumps(hub.config, indent=2, ensure_ascii=False))

            elif args.config_cmd == 'set':
                # 简单的配置设置
                if args.key == 'enabled':
                    hub.config['enabled'] = args.value.lower() in ('true', '1', 'yes')
                elif args.key == 'throttle_seconds':
                    hub.config['throttle_seconds'] = int(args.value)
                else:
                    print("Unknown config key: {}".format(args.key))
                    return 1

                hub.save_config()
                print("Config updated: {} = {}".format(args.key, args.value))

            elif args.config_cmd == 'init':
                hub.config = hub._get_default_config()
                hub.save_config()
                print("Default config created at: {}".format(hub.config_path))

            else:
                p_config.print_help()

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print("Error: {}".format(e), file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
