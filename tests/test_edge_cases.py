#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Edge case tests for Intelligent Engine
Tests boundary conditions and error handling
"""

import sys
from pathlib import Path
from time import time

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from intelligent_engine import (
    PatternDetector,
    ShortTermMemory,
    StrategySelector,
    AdaptiveOptimizer,
    IntelligentEngine,
    Event
)


class TestPatternDetectorEdgeCases:
    """Test PatternDetector edge cases"""

    def test_zero_window_size(self):
        """Test that zero window size raises ValueError"""
        with pytest.raises(ValueError, match="window_size must be positive"):
            PatternDetector(window_size=0)

    def test_negative_window_size(self):
        """Test that negative window size raises ValueError"""
        with pytest.raises(ValueError, match="window_size must be positive"):
            PatternDetector(window_size=-5)

    def test_single_event_no_detection(self):
        """Test that single event doesn't trigger any detection"""
        detector = PatternDetector()
        detector.add_event(Event(
            timestamp=time(),
            event_type='output',
            content='Test output'
        ))

        assert detector.detect_loop() is None
        assert detector.detect_repetition() is None
        assert detector.detect_error_pattern() is None

    def test_empty_content_handling(self):
        """Test handling of empty content"""
        detector = PatternDetector()

        # Should not crash with empty content
        detector.add_event(Event(
            timestamp=time(),
            event_type='output',
            content=''
        ))

        assert detector.detect_repetition() is None

    def test_very_long_content(self):
        """Test handling of very long content"""
        detector = PatternDetector()

        long_content = "A" * 10000
        detector.add_event(Event(
            timestamp=time(),
            event_type='output',
            content=long_content
        ))

        # Should handle gracefully
        context = detector.get_recent_context(1)
        assert len(context) == 1

    def test_unicode_content(self):
        """Test handling of unicode content"""
        detector = PatternDetector()

        detector.add_event(Event(
            timestamp=time(),
            event_type='output',
            content='测试中文 🧠 智能引擎'
        ))

        # Should not crash
        context = detector.get_recent_context(1)
        assert len(context) == 1


class TestShortTermMemoryEdgeCases:
    """Test ShortTermMemory edge cases"""

    def test_max_outcomes_per_decision(self):
        """Test that outcomes are limited per decision"""
        memory = ShortTermMemory(max_items=10, max_outcomes_per_decision=3)

        memory.add_decision('dec1', 'wait', {})
        for i in range(10):
            memory.record_outcome('dec1', i % 2 == 0)

        # Should only keep max_outcomes_per_decision (3)
        outcomes = memory.outcomes.get('dec1')
        assert len(outcomes) <= 3

    def test_record_outcome_before_decision(self):
        """Test recording outcome before decision exists"""
        memory = ShortTermMemory()

        # Should not crash
        memory.record_outcome('nonexistent', True)

        # Should not create invalid state
        assert 'nonexistent' not in memory.strategy_performance

    def test_missing_strategy_in_performance(self):
        """Test handling of missing strategy in performance tracking"""
        memory = ShortTermMemory()

        # Manually create decision without strategy performance
        memory.decisions.append({
            'id': 'dec1',
            'timestamp': time(),
            'strategy': 'unknown_strategy',
            'context': {}
        })

        # Should create new entry
        memory.record_outcome('dec1', True)

        assert 'unknown_strategy' in memory.strategy_performance
        assert memory.strategy_performance['unknown_strategy']['success'] == 1


class TestStrategySelectorEdgeCases:
    """Test StrategySelector edge cases"""

    def test_unknown_pattern_type(self):
        """Test handling of unknown pattern type"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)

        unknown_pattern = {'type': 'unknown_type', 'severity': 'high'}

        # Should default to wait strategy
        strategy = selector.select_strategy(unknown_pattern)
        assert strategy in ['wait', 'nudge']

    def test_pattern_without_severity(self):
        """Test handling of pattern without severity"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)

        pattern = {'type': 'stagnation'}  # no severity

        # Should still work
        recommendation = selector.recommend_action(pattern)
        assert 'strategy' in recommendation


class TestAdaptiveOptimizerEdgeCases:
    """Test AdaptiveOptimizer edge cases"""

    def test_negative_aggressiveness(self):
        """Test that negative aggressiveness is clamped"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory, initial_aggressiveness=-0.5)

        # Should work, but may stay at 0
        assert optimizer.aggressiveness >= 0

    def test_aggressiveness_above_one(self):
        """Test that aggressiveness above 1 is handled"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory, initial_aggressiveness=1.5)

        # Should work
        assert optimizer.aggressiveness <= 1.5

    def test_should_intervene_with_no_pattern(self):
        """Test should_intervene with no pattern"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory)

        # Should not intervene without pattern
        assert optimizer.should_intervene(None) is False


class TestIntelligentEngineEdgeCases:
    """Test IntelligentEngine edge cases"""

    def test_engine_with_zero_aggressiveness(self):
        """Test engine with zero aggressiveness"""
        engine = IntelligentEngine(initial_aggressiveness=0.0)

        status = engine.get_status()
        assert status['aggressiveness'] == 0.0

    def test_engine_with_max_aggressiveness(self):
        """Test engine with maximum aggressiveness"""
        engine = IntelligentEngine(initial_aggressiveness=1.0)

        status = engine.get_status()
        assert status['aggressiveness'] == 1.0

    def test_add_invalid_event_type(self):
        """Test adding event with invalid type"""
        engine = IntelligentEngine()

        # Should not crash
        engine.add_event('invalid_type', 'content')

        # Should still work
        status = engine.get_status()
        assert 'pattern_detector' in status

    def test_analyze_with_empty_context(self):
        """Test analyze with no prior events"""
        engine = IntelligentEngine()

        # Should still work
        recommendation = engine.analyze_and_recommend('unknown')
        assert 'strategy' in recommendation

    def test_record_decision_with_outcome_mismatch(self):
        """Test recording outcome for non-existent decision"""
        engine = IntelligentEngine()

        # Record outcome for non-existent decision
        engine.record_outcome('nonexistent', True)

        # Should not crash
        status = engine.get_status()
        assert 'memory' in status


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
