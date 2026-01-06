#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Intelligent Engine for Smart Monitor
Provides lightweight AI capabilities for pattern detection and adaptive decision-making

Features:
1. Pattern Detection: Loop detection, repetition detection, error pattern detection
2. Short-term Memory: Track recent outputs and decisions
3. Strategy Selection: Context-aware strategy selection
4. Adaptive Optimization: Behavior adjustment based on success rates
"""

import json
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from base import DataClassMixin


@dataclass
class Event(DataClassMixin):
    """Represents a single event in the monitoring timeline"""
    timestamp: float
    event_type: str  # 'output', 'decision', 'error', 'command', 'idle'
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'event_type': self.event_type,
            'content': self.content,
            'metadata': self.metadata
        }


class PatternDetector:
    """Detect patterns in monitoring events to identify loops and issues"""

    def __init__(self, window_size: int = 20):
        """
        Initialize pattern detector

        Args:
            window_size: Number of recent events to analyze
        """
        if window_size <= 0:
            raise ValueError(f"window_size must be positive, got {window_size}")
        self.events: deque = deque(maxlen=window_size)
        self.loop_threshold = 3  # Number of repetitions to consider a loop
        self.similarity_threshold = 0.7  # Similarity threshold for repetition detection

    def add_event(self, event: Event) -> None:
        """Add a new event to the pattern detector"""
        self.events.append(event)

    def detect_loop(self) -> Optional[Dict[str, Any]]:
        """
        Detect if we're in a loop (repeating same outputs/commands)

        Returns:
            Loop detection result with type, pattern, and count, or None
        """
        if len(self.events) < self.loop_threshold:
            return None

        # Check last N events for exact repetitions
        recent_events = list(self.events)[-self.loop_threshold:]
        outputs = [e for e in recent_events if e.event_type in ['output', 'command']]

        if len(outputs) >= self.loop_threshold:
            contents = [self._normalize_content(e.content) for e in outputs]
            # Check if all are the same (exact loop)
            if len(set(contents)) == 1:
                return {
                    'type': 'exact_loop',
                    'pattern': outputs[0].content[:100],
                    'count': len(outputs),
                    'severity': 'high',
                    'suggestion': 'Breaking loop - suggest alternative approach'
                }

        return None

    def detect_repetition(self) -> Optional[Dict[str, Any]]:
        """
        Detect if there are repetitive patterns (not exact loops)

        Returns:
            Repetition detection result, or None
        """
        if len(self.events) < 5:
            return None

        recent_events = list(self.events)[-10:]
        outputs = [e for e in recent_events if e.event_type == 'output']

        if len(outputs) < 3:
            return None

        # Check for similar patterns using first words and structure
        patterns = []
        for output in outputs:
            # Extract first two words and ignore numbers/variables
            words = output.content.strip().split()
            if len(words) >= 2:
                # Create pattern from first two words, replacing digits with wildcard
                first_word = words[0]
                second_word = '*' if len(words) > 1 and words[1].isdigit() else (words[1] if len(words) > 1 else '')
                pattern = f'{first_word} {second_word}'.strip()
                patterns.append(pattern)
            else:
                patterns.append(output.content[:20] if output.content else '')

        # Count pattern repetitions
        pattern_counts = {}
        for pattern in patterns:
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

        # If any pattern appears 3+ times
        for pattern, count in pattern_counts.items():
            if count >= 3:
                return {
                    'type': 'repetition',
                    'pattern': pattern,
                    'count': count,
                    'severity': 'medium',
                    'suggestion': f'Repetitive pattern detected: "{pattern}"'
                }

        return None

    def detect_error_pattern(self) -> Optional[Dict[str, Any]]:
        """
        Detect if we're seeing repeated errors

        Returns:
            Error pattern detection result, or None
        """
        error_events = [e for e in self.events if e.event_type == 'error']

        if len(error_events) < 2:
            return None

        # Group errors by similarity
        error_groups = {}
        for error in error_events:
            # Extract error type (first line or key phrase)
            error_type = self._extract_error_type(error.content)
            if error_type not in error_groups:
                error_groups[error_type] = []
            error_groups[error_type].append(error)

        # Check for repeated error types
        for error_type, errors in error_groups.items():
            if len(errors) >= 2:
                return {
                    'type': 'error_loop',
                    'error_type': error_type,
                    'count': len(errors),
                    'severity': 'high',
                    'suggestion': f'Repeated error: {error_type} - suggest investigation'
                }

        return None

    def detect_stagnation(self, idle_threshold_seconds: float = 30.0, current_time: float = None) -> Optional[Dict[str, Any]]:
        """
        Detect if the system is stuck (idle for too long)

        Args:
            idle_threshold_seconds: Seconds of inactivity to consider stagnation
            current_time: Current timestamp (for testing, defaults to now)

        Returns:
            Stagnation detection result, or None
        """
        if len(self.events) < 1:
            return None

        now = current_time if current_time is not None else datetime.now().timestamp()
        idle_events = [e for e in self.events if e.event_type == 'idle']

        if not idle_events:
            return None

        last_idle = idle_events[-1]
        idle_duration = now - last_idle.timestamp

        if idle_duration >= idle_threshold_seconds:
            return {
                'type': 'stagnation',
                'idle_duration': idle_duration,
                'severity': 'medium' if idle_duration < 60 else 'high',
                'suggestion': f'System idle for {idle_duration:.0f}s - suggest intervention'
            }

        return None

    def get_recent_context(self, count: int = 5) -> List[Dict[str, Any]]:
        """Get recent events for context"""
        recent = list(self.events)[-count:]
        return [e.to_dict() for e in recent]

    def _normalize_content(self, content: str) -> str:
        """Normalize content for comparison (remove timestamps, whitespace)"""
        # Remove common prefixes/timestamps
        patterns = [
            r'^\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}',
            r'^\[\d+:\d+:\d+\]',
            r'^\s+',
        ]
        normalized = content
        for pattern in patterns:
            normalized = re.sub(pattern, '', normalized, flags=re.MULTILINE)
        return normalized.strip()

    def _extract_error_type(self, error_content: str) -> str:
        """Extract error type from error message"""
        # Common error patterns
        error_patterns = [
            r'(SyntaxError|IndentationError|NameError|TypeError|ValueError|AttributeError|KeyError|ImportError)',
            r'command not found',
            r'No such file or directory',
            r'permission denied',
            r'Connection refused',
            r'timeout',
        ]

        for pattern in error_patterns:
            match = re.search(pattern, error_content, re.IGNORECASE)
            if match:
                return match.group(1) if match.groups() else match.group(0)

        # First line or first 30 chars
        lines = error_content.strip().split('\n')
        return lines[0][:30] if lines else error_content[:30]


class ShortTermMemory:
    """Short-term memory system for tracking recent decisions and outcomes"""

    def __init__(self, max_items: int = 50, max_outcomes_per_decision: int = 10):
        """
        Initialize short-term memory

        Args:
            max_items: Maximum number of items to remember
            max_outcomes_per_decision: Maximum outcomes to keep per decision
        """
        self.decisions: deque = deque(maxlen=max_items)
        self.outcomes: Dict[str, deque] = {}  # decision_id -> outcomes (deque with maxlen)
        self.max_outcomes_per_decision = max_outcomes_per_decision
        self.strategy_performance: Dict[str, Dict] = {}  # strategy -> stats

    def add_decision(self, decision_id: str, strategy: str, context: Dict) -> None:
        """Record a decision made by the monitor"""
        self.decisions.append({
            'id': decision_id,
            'timestamp': datetime.now().timestamp(),
            'strategy': strategy,
            'context': context
        })

        # Initialize strategy stats if needed
        if strategy not in self.strategy_performance:
            self.strategy_performance[strategy] = {
                'used': 0,
                'success': 0,
                'failure': 0
            }
        self.strategy_performance[strategy]['used'] += 1

    def record_outcome(self, decision_id: str, success: bool, feedback: str = '') -> None:
        """Record the outcome of a decision"""
        if decision_id not in self.outcomes:
            self.outcomes[decision_id] = deque(maxlen=self.max_outcomes_per_decision)

        self.outcomes[decision_id].append({
            'timestamp': datetime.now().timestamp(),
            'success': success,
            'feedback': feedback
        })

        # Update strategy performance with defensive checks
        for decision in reversed(self.decisions):
            if decision.get('id') == decision_id:
                strategy = decision.get('strategy')
                if strategy:
                    if strategy not in self.strategy_performance:
                        # Initialize strategy stats if not exists
                        self.strategy_performance[strategy] = {
                            'used': 0,
                            'success': 0,
                            'failure': 0
                        }
                    if success:
                        self.strategy_performance[strategy]['success'] += 1
                    else:
                        self.strategy_performance[strategy]['failure'] += 1
                break

    def get_recent_decisions(self, count: int = 5) -> List[Dict]:
        """Get recent decisions for context"""
        recent = list(self.decisions)[-count:]
        return recent

    def get_strategy_performance(self, strategy: str) -> Optional[Dict]:
        """Get performance stats for a strategy"""
        return self.strategy_performance.get(strategy)

    def get_best_strategy(self) -> Optional[str]:
        """Get the best performing strategy (highest success rate)"""
        best_strategy = None
        best_rate = 0.0

        for strategy, stats in self.strategy_performance.items():
            used = stats.get('used', 0)
            if used >= 2:  # Minimum usage threshold
                success = stats.get('success', 0)
                rate = success / used  # used >= 2 ensures no division by zero
                if rate > best_rate:
                    best_rate = rate
                    best_strategy = strategy

        return best_strategy

    def get_context_summary(self) -> Dict[str, Any]:
        """Get summary of current context"""
        return {
            'recent_decisions_count': len(self.decisions),
            'tracked_outcomes': len(self.outcomes),
            'strategies_tried': list(self.strategy_performance.keys()),
            'best_strategy': self.get_best_strategy()
        }


class StrategySelector:
    """Context-aware strategy selection based on patterns and history"""

    # Strategy definitions
    STRATEGIES = {
        'wait': {'aggressiveness': 0, 'intervention': 'none'},
        'nudge': {'aggressiveness': 0.3, 'intervention': 'gentle'},
        'command': {'aggressiveness': 0.7, 'intervention': 'direct'},
        'ask': {'aggressiveness': 0.5, 'intervention': 'query'},
        'escalate': {'aggressiveness': 1.0, 'intervention': 'urgent'},
    }

    def __init__(self, memory: ShortTermMemory):
        """
        Initialize strategy selector

        Args:
            memory: Short-term memory for historical context
        """
        self.memory = memory
        self.context_rules = self._init_context_rules()

    def _init_context_rules(self) -> Dict[str, Dict]:
        """Initialize context-based strategy rules"""
        return {
            'exact_loop': {
                'preferred_strategies': ['ask', 'escalate'],
                'avoid_strategies': ['wait'],
                'reason': 'Loop detected - need intervention'
            },
            'repetition': {
                'preferred_strategies': ['nudge', 'ask'],
                'avoid_strategies': ['wait', 'command'],
                'reason': 'Repetition detected - suggest alternative'
            },
            'error_loop': {
                'preferred_strategies': ['escalate', 'ask'],
                'avoid_strategies': ['wait', 'command'],
                'reason': 'Error loop - escalate to user'
            },
            'stagnation': {
                'preferred_strategies': ['nudge', 'command'],
                'avoid_strategies': ['wait'],
                'reason': 'System idle - need action'
            },
            'normal': {
                'preferred_strategies': ['wait', 'nudge'],
                'avoid_strategies': [],
                'reason': 'Normal operation'
            }
        }

    def select_strategy(
        self,
        pattern: Optional[Dict] = None,
        current_stage: str = 'unknown',
        aggressiveness: float = 0.5
    ) -> str:
        """
        Select appropriate strategy based on context

        Args:
            pattern: Detected pattern (from PatternDetector)
            current_stage: Current workflow stage
            aggressiveness: Desired aggressiveness level (0.0-1.0)

        Returns:
            Selected strategy name
        """
        # Determine context from pattern
        context_type = pattern['type'] if pattern else 'normal'

        # Get rules for this context
        rules = self.context_rules.get(context_type, self.context_rules['normal'])

        # Get candidate strategies
        preferred = rules['preferred_strategies']
        avoid = rules['avoid_strategies']

        # Filter by aggressiveness
        candidates = []
        for strategy in preferred:
            if strategy in self.STRATEGIES:
                strat_agg = self.STRATEGIES[strategy]['aggressiveness']
                # Allow if strategy aggressiveness is within tolerance
                if abs(strat_agg - aggressiveness) <= 0.3:
                    candidates.append((strategy, strat_agg))

        # Sort by closeness to desired aggressiveness
        candidates.sort(key=lambda x: abs(x[1] - aggressiveness))

        if candidates:
            return candidates[0][0]

        # Fallback to first preferred strategy
        return preferred[0] if preferred else 'wait'

    def recommend_action(self, pattern: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Recommend an action based on pattern detection

        Args:
            pattern: Detected pattern

        Returns:
            Action recommendation with strategy and reasoning
        """
        if not pattern:
            return {
                'strategy': 'wait',
                'reason': 'No pattern detected - continue monitoring',
                'confidence': 0.5
            }

        severity = pattern.get('severity', 'low')
        pattern_type = pattern['type']

        # High severity patterns need more aggressive response
        if severity == 'high':
            strategy = self.select_strategy(pattern, aggressiveness=0.8)
            confidence = 0.8
        elif severity == 'medium':
            strategy = self.select_strategy(pattern, aggressiveness=0.5)
            confidence = 0.6
        else:
            strategy = self.select_strategy(pattern, aggressiveness=0.3)
            confidence = 0.4

        return {
            'strategy': strategy,
            'reason': pattern.get('suggestion', f'Based on {pattern_type}'),
            'confidence': confidence,
            'pattern': pattern
        }


class AdaptiveOptimizer:
    """Adaptive behavior adjustment based on success rates and feedback"""

    def __init__(self, memory: ShortTermMemory, initial_aggressiveness: float = 0.5):
        """
        Initialize adaptive optimizer

        Args:
            memory: Short-term memory for performance tracking
            initial_aggressiveness: Starting aggressiveness level (0.0-1.0)
        """
        self.memory = memory
        # Clamp aggressiveness to [0.0, 1.0]
        self.aggressiveness = max(0.0, min(1.0, initial_aggressiveness))
        self.adjustment_interval = 10  # Adjust after N outcomes
        self.outcome_count = 0

    def record_outcome(self, decision_id: str, success: bool, feedback: str = '') -> None:
        """Record an outcome and periodically adjust behavior"""
        self.memory.record_outcome(decision_id, success, feedback)
        self.outcome_count += 1

        # Adjust periodically
        if self.outcome_count % self.adjustment_interval == 0:
            self._adjust_behavior()

    def _adjust_behavior(self) -> None:
        """Adjust aggressiveness based on recent performance"""
        summary = self.memory.get_context_summary()

        # Calculate overall success rate
        total_success = 0
        total_used = 0
        for stats in self.memory.strategy_performance.values():
            total_success += stats['success']
            total_used += stats['used']

        if total_used == 0:
            return

        success_rate = total_success / total_used

        # Adjust aggressiveness based on success rate
        # High success rate -> can be more aggressive
        # Low success rate -> should be more conservative
        if success_rate > 0.8:
            self.aggressiveness = min(1.0, self.aggressiveness + 0.1)
        elif success_rate < 0.5:
            self.aggressiveness = max(0.0, self.aggressiveness - 0.1)

    def get_current_aggressiveness(self) -> float:
        """Get current aggressiveness level"""
        return self.aggressiveness

    def should_intervene(self, pattern: Optional[Dict] = None) -> bool:
        """
        Decide whether to intervene based on pattern and aggressiveness

        Args:
            pattern: Detected pattern

        Returns:
            True if intervention is recommended
        """
        if not pattern:
            return False

        severity = pattern.get('severity', 'low')

        # High severity always intervenes
        if severity == 'high':
            return True

        # Medium severity intervenes based on aggressiveness
        if severity == 'medium':
            return self.aggressiveness > 0.4

        # Low severity rarely intervenes
        return self.aggressiveness > 0.7


class IntelligentEngine:
    """Main intelligent engine coordinating all AI components"""

    def __init__(
        self,
        pattern_window_size: int = 20,
        memory_max_items: int = 50,
        initial_aggressiveness: float = 0.5
    ):
        """
        Initialize intelligent engine

        Args:
            pattern_window_size: Pattern detection window size
            memory_max_items: Short-term memory capacity
            initial_aggressiveness: Initial aggressiveness level (0.0-1.0)
        """
        self.pattern_detector = PatternDetector(window_size=pattern_window_size)
        self.memory = ShortTermMemory(max_items=memory_max_items)
        self.strategy_selector = StrategySelector(self.memory)
        self.optimizer = AdaptiveOptimizer(self.memory)
        self.optimizer.aggressiveness = initial_aggressiveness

    def add_event(self, event_type: str, content: str, metadata: Dict = None) -> None:
        """Add an event to the intelligent engine"""
        event = Event(
            timestamp=datetime.now().timestamp(),
            event_type=event_type,
            content=content,
            metadata=metadata or {}
        )
        self.pattern_detector.add_event(event)

    def analyze_and_recommend(self, current_stage: str = 'unknown') -> Dict[str, Any]:
        """
        Analyze current state and recommend action

        Args:
            current_stage: Current workflow stage

        Returns:
            Recommendation with strategy, reasoning, and confidence
        """
        # Detect patterns
        patterns = [
            self.pattern_detector.detect_loop(),
            self.pattern_detector.detect_repetition(),
            self.pattern_detector.detect_error_pattern(),
            self.pattern_detector.detect_stagnation()
        ]

        # Get first detected pattern (prioritize by severity)
        detected_pattern = None
        for pattern in patterns:
            if pattern:
                detected_pattern = pattern
                break

        # Get recommendation
        recommendation = self.strategy_selector.recommend_action(detected_pattern)

        # Add context
        recommendation['context'] = {
            'stage': current_stage,
            'aggressiveness': self.optimizer.get_current_aggressiveness(),
            'recent_context': self.pattern_detector.get_recent_context(3),
            'memory_summary': self.memory.get_context_summary()
        }

        return recommendation

    def record_decision(
        self,
        decision_id: str,
        strategy: str,
        context: Dict
    ) -> None:
        """Record a decision made"""
        self.memory.add_decision(decision_id, strategy, context)

    def record_outcome(self, decision_id: str, success: bool, feedback: str = '') -> None:
        """Record outcome of a decision"""
        self.optimizer.record_outcome(decision_id, success, feedback)

    def get_status(self) -> Dict[str, Any]:
        """Get current engine status"""
        return {
            'aggressiveness': self.optimizer.get_current_aggressiveness(),
            'pattern_detector': {
                'events_count': len(self.pattern_detector.events),
                'recent_patterns': [
                    self.pattern_detector.detect_loop(),
                    self.pattern_detector.detect_repetition(),
                    self.pattern_detector.detect_error_pattern(),
                    self.pattern_detector.detect_stagnation()
                ]
            },
            'memory': self.memory.get_context_summary(),
            'strategy_performance': self.memory.strategy_performance
        }


# CLI interface for standalone testing
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Intelligent Engine for Smart Monitor')
    parser.add_argument('--analyze', action='store_true', help='Analyze and recommend')
    parser.add_argument('--status', action='store_true', help='Show engine status')
    parser.add_argument('--stage', default='unknown', help='Current stage')
    args = parser.parse_args()

    engine = IntelligentEngine()

    # Add some test events
    engine.add_event('output', 'Starting task...')
    engine.add_event('output', 'Processing...')
    engine.add_event('error', 'SyntaxError: invalid syntax')
    engine.add_event('output', 'Starting task...')
    engine.add_event('output', 'Processing...')
    engine.add_event('error', 'SyntaxError: invalid syntax')

    if args.status:
        status = engine.get_status()
        print(json.dumps(status, indent=2))

    if args.analyze:
        recommendation = engine.analyze_and_recommend(args.stage)
        print(json.dumps(recommendation, indent=2))
