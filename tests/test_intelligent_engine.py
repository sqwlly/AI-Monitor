#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test suite for Intelligent Engine
Tests pattern detection, short-term memory, strategy selection, and adaptive optimization
"""

import sys
from pathlib import Path
from time import sleep, time

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from intelligent_engine import (
    Event,
    PatternDetector,
    ShortTermMemory,
    StrategySelector,
    AdaptiveOptimizer,
    IntelligentEngine
)


class TestEvent:
    """Test Event dataclass"""

    def test_event_creation(self):
        """Test creating an event"""
        event = Event(
            timestamp=time(),
            event_type='output',
            content='Test output',
            metadata={'source': 'test'}
        )
        assert event.event_type == 'output'
        assert event.content == 'Test output'
        assert event.metadata['source'] == 'test'

    def test_event_to_dict(self):
        """Test event serialization"""
        event = Event(
            timestamp=time(),
            event_type='error',
            content='Error message'
        )
        data = event.to_dict()
        assert 'timestamp' in data
        assert data['event_type'] == 'error'
        assert data['content'] == 'Error message'


class TestPatternDetector:
    """Test pattern detection functionality"""

    def test_init(self):
        """Test detector initialization"""
        detector = PatternDetector(window_size=10)
        assert len(detector.events) == 0
        assert detector.loop_threshold == 3

    def test_add_event(self):
        """Test adding events"""
        detector = PatternDetector()
        event = Event(timestamp=time(), event_type='output', content='Test')
        detector.add_event(event)
        assert len(detector.events) == 1

    def test_detect_exact_loop(self):
        """Test exact loop detection"""
        detector = PatternDetector(window_size=10)

        # Add same output 3 times
        for _ in range(3):
            detector.add_event(Event(
                timestamp=time(),
                event_type='output',
                content='Same output repeated'
            ))

        pattern = detector.detect_loop()
        assert pattern is not None
        assert pattern['type'] == 'exact_loop'
        assert pattern['count'] == 3
        assert pattern['severity'] == 'high'

    def test_detect_no_loop_with_different_outputs(self):
        """Test that loop is not detected with different outputs"""
        detector = PatternDetector()

        for i in range(3):
            detector.add_event(Event(
                timestamp=time(),
                event_type='output',
                content=f'Different output {i}'
            ))

        pattern = detector.detect_loop()
        assert pattern is None

    def test_detect_repetition(self):
        """Test repetition detection"""
        detector = PatternDetector(window_size=20)

        # Add similar outputs (same first word "Error")
        for i in range(5):
            detector.add_event(Event(
                timestamp=time(),
                event_type='output',
                content=f'Error in module {i}'
            ))

        pattern = detector.detect_repetition()
        assert pattern is not None
        assert pattern['type'] == 'repetition'
        assert pattern['count'] >= 3

    def test_detect_error_loop(self):
        """Test error loop detection"""
        detector = PatternDetector()

        # Add same error twice
        for _ in range(2):
            detector.add_event(Event(
                timestamp=time(),
                event_type='error',
                content='SyntaxError: invalid syntax'
            ))

        pattern = detector.detect_error_pattern()
        assert pattern is not None
        assert pattern['type'] == 'error_loop'
        assert pattern['count'] == 2
        assert 'SyntaxError' in pattern['error_type']

    def test_detect_stagnation(self):
        """Test stagnation detection"""
        detector = PatternDetector()

        # Add idle event at timestamp 0
        detector.add_event(Event(
            timestamp=0,
            event_type='idle',
            content='System idle'
        ))

        # Simulate 35 seconds later
        pattern = detector.detect_stagnation(
            idle_threshold_seconds=30,
            current_time=35
        )
        assert pattern is not None
        assert pattern['type'] == 'stagnation'
        assert pattern['idle_duration'] >= 30

    def test_no_stagnation_when_recent_activity(self):
        """Test no stagnation with recent activity"""
        detector = PatternDetector()

        # Add recent idle event
        detector.add_event(Event(
            timestamp=time(),
            event_type='idle',
            content='System idle'
        ))

        pattern = detector.detect_stagnation(idle_threshold_seconds=30)
        assert pattern is None

    def test_get_recent_context(self):
        """Test getting recent context"""
        detector = PatternDetector()

        for i in range(5):
            detector.add_event(Event(
                timestamp=time(),
                event_type='output',
                content=f'Output {i}'
            ))

        context = detector.get_recent_context(3)
        assert len(context) == 3
        assert all('timestamp' in c for c in context)


class TestShortTermMemory:
    """Test short-term memory functionality"""

    def test_init(self):
        """Test memory initialization"""
        memory = ShortTermMemory(max_items=50)
        assert len(memory.decisions) == 0
        assert len(memory.outcomes) == 0

    def test_add_decision(self):
        """Test adding a decision"""
        memory = ShortTermMemory()

        memory.add_decision(
            decision_id='dec1',
            strategy='nudge',
            context={'stage': 'implementation'}
        )

        assert len(memory.decisions) == 1
        assert memory.decisions[0]['id'] == 'dec1'
        assert memory.decisions[0]['strategy'] == 'nudge'

    def test_record_outcome(self):
        """Test recording an outcome"""
        memory = ShortTermMemory()

        memory.add_decision('dec1', 'wait', {})
        memory.record_outcome('dec1', success=True, feedback='Worked well')

        assert 'dec1' in memory.outcomes
        assert len(memory.outcomes['dec1']) == 1
        assert memory.outcomes['dec1'][0]['success'] is True

    def test_strategy_performance_tracking(self):
        """Test strategy performance tracking"""
        memory = ShortTermMemory()

        memory.add_decision('dec1', 'wait', {})
        memory.record_outcome('dec1', success=True)

        memory.add_decision('dec2', 'wait', {})
        memory.record_outcome('dec2', success=False)

        stats = memory.get_strategy_performance('wait')
        assert stats is not None
        assert stats['used'] == 2
        assert stats['success'] == 1
        assert stats['failure'] == 1

    def test_get_best_strategy(self):
        """Test getting best performing strategy"""
        memory = ShortTermMemory()

        # Add some decisions with outcomes
        memory.add_decision('dec1', 'wait', {})
        memory.record_outcome('dec1', success=True)

        memory.add_decision('dec2', 'wait', {})
        memory.record_outcome('dec2', success=True)

        memory.add_decision('dec3', 'command', {})
        memory.record_outcome('dec3', success=False)

        best = memory.get_best_strategy()
        assert best == 'wait'  # 100% success rate

    def test_get_recent_decisions(self):
        """Test getting recent decisions"""
        memory = ShortTermMemory()

        for i in range(5):
            memory.add_decision(f'dec{i}', 'wait', {})

        recent = memory.get_recent_decisions(3)
        assert len(recent) == 3
        assert recent[0]['id'] == 'dec2'  # Last 3 of 5
        assert recent[2]['id'] == 'dec4'

    def test_context_summary(self):
        """Test getting context summary"""
        memory = ShortTermMemory()

        memory.add_decision('dec1', 'wait', {})
        memory.add_decision('dec2', 'nudge', {})

        summary = memory.get_context_summary()
        assert 'recent_decisions_count' in summary
        assert summary['recent_decisions_count'] == 2
        assert 'wait' in summary['strategies_tried']


class TestStrategySelector:
    """Test strategy selection functionality"""

    def test_init(self):
        """Test selector initialization"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)
        assert selector.memory is memory
        assert len(selector.context_rules) > 0

    def test_select_strategy_for_loop(self):
        """Test strategy selection for loop pattern"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)

        loop_pattern = {
            'type': 'exact_loop',
            'severity': 'high'
        }

        strategy = selector.select_strategy(loop_pattern, aggressiveness=0.8)
        assert strategy in ['ask', 'escalate']  # Preferred for loops

    def test_select_strategy_for_stagnation(self):
        """Test strategy selection for stagnation"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)

        stagnation_pattern = {
            'type': 'stagnation',
            'severity': 'medium'
        }

        strategy = selector.select_strategy(stagnation_pattern, aggressiveness=0.5)
        assert strategy in ['nudge', 'command']  # Preferred for stagnation

    def test_select_strategy_normal(self):
        """Test strategy selection for normal operation"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)

        strategy = selector.select_strategy(None, aggressiveness=0.3)
        assert strategy in ['wait', 'nudge']  # Preferred for normal

    def test_recommend_action_for_pattern(self):
        """Test action recommendation with pattern"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)

        pattern = {
            'type': 'error_loop',
            'severity': 'high',
            'suggestion': 'Escalate to user'
        }

        recommendation = selector.recommend_action(pattern)
        assert 'strategy' in recommendation
        assert 'reason' in recommendation
        assert 'confidence' in recommendation
        assert recommendation['confidence'] > 0.5  # High severity = high confidence

    def test_recommend_action_no_pattern(self):
        """Test action recommendation without pattern"""
        memory = ShortTermMemory()
        selector = StrategySelector(memory)

        recommendation = selector.recommend_action(None)
        assert recommendation['strategy'] == 'wait'
        assert 'No pattern detected' in recommendation['reason']


class TestAdaptiveOptimizer:
    """Test adaptive optimization functionality"""

    def test_init(self):
        """Test optimizer initialization"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory)
        assert optimizer.aggressiveness == 0.5
        assert optimizer.outcome_count == 0

    def test_record_outcome(self):
        """Test recording outcome"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory)

        memory.add_decision('dec1', 'wait', {})
        optimizer.record_outcome('dec1', success=True)

        assert optimizer.outcome_count == 1

    def test_aggressiveness_adjustment_up(self):
        """Test aggressiveness increases with high success rate"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory, initial_aggressiveness=0.5)

        # Record 10 successful outcomes
        for i in range(10):
            memory.add_decision(f'dec{i}', 'wait', {})
            optimizer.record_outcome(f'dec{i}', success=True)

        # Should have adjusted after 10 outcomes
        assert optimizer.aggressiveness > 0.5

    def test_aggressiveness_adjustment_down(self):
        """Test aggressiveness decreases with low success rate"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory, initial_aggressiveness=0.5)

        # Record 10 failed outcomes
        for i in range(10):
            memory.add_decision(f'dec{i}', 'command', {})
            optimizer.record_outcome(f'dec{i}', success=False)

        # Should have adjusted after 10 outcomes
        assert optimizer.aggressiveness < 0.5

    def test_should_intervene_high_severity(self):
        """Test intervention decision for high severity"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory)

        pattern = {'severity': 'high'}
        assert optimizer.should_intervene(pattern) is True

    def test_should_intervene_medium_severity(self):
        """Test intervention decision for medium severity"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory, initial_aggressiveness=0.6)

        pattern = {'severity': 'medium'}
        assert optimizer.should_intervene(pattern) is True  # 0.6 > 0.4 threshold

    def test_should_intervene_low_severity(self):
        """Test intervention decision for low severity"""
        memory = ShortTermMemory()
        optimizer = AdaptiveOptimizer(memory, initial_aggressiveness=0.5)

        pattern = {'severity': 'low'}
        assert optimizer.should_intervene(pattern) is False  # Not aggressive enough


class TestIntelligentEngine:
    """Test main intelligent engine"""

    def test_init(self):
        """Test engine initialization"""
        engine = IntelligentEngine()
        assert engine.pattern_detector is not None
        assert engine.memory is not None
        assert engine.strategy_selector is not None
        assert engine.optimizer is not None

    def test_add_event(self):
        """Test adding events"""
        engine = IntelligentEngine()
        engine.add_event('output', 'Test output')

        assert len(engine.pattern_detector.events) == 1

    def test_analyze_and_recommend_no_pattern(self):
        """Test analysis and recommendation without patterns"""
        engine = IntelligentEngine()

        recommendation = engine.analyze_and_recommend('implementation')
        assert 'strategy' in recommendation
        assert 'context' in recommendation
        assert recommendation['context']['stage'] == 'implementation'

    def test_analyze_and_recommend_with_loop(self):
        """Test analysis and recommendation with loop detection"""
        engine = IntelligentEngine()

        # Add loop pattern
        for _ in range(3):
            engine.add_event('output', 'Same output')

        recommendation = engine.analyze_and_recommend()
        # Should detect loop and recommend non-wait strategy
        assert recommendation['strategy'] in ['ask', 'escalate', 'nudge']

    def test_record_decision_and_outcome(self):
        """Test recording decisions and outcomes"""
        engine = IntelligentEngine()

        engine.record_decision('dec1', 'wait', {'stage': 'test'})
        engine.record_outcome('dec1', success=True)

        # Check memory recorded
        assert len(engine.memory.decisions) == 1
        assert 'dec1' in engine.memory.outcomes

    def test_get_status(self):
        """Test getting engine status"""
        engine = IntelligentEngine()

        engine.add_event('output', 'Test')
        engine.record_decision('dec1', 'wait', {})

        status = engine.get_status()
        assert 'aggressiveness' in status
        assert 'pattern_detector' in status
        assert 'memory' in status
        assert 'strategy_performance' in status

    def test_full_workflow(self):
        """Test full intelligent workflow"""
        engine = IntelligentEngine()

        # Simulate a monitoring session
        engine.add_event('output', 'Starting implementation...')
        engine.add_event('output', 'Writing code...')
        engine.add_event('error', 'SyntaxError: invalid syntax')
        engine.add_event('output', 'Starting implementation...')
        engine.add_event('output', 'Writing code...')
        engine.add_event('error', 'SyntaxError: invalid syntax')

        # Analyze
        recommendation = engine.analyze_and_recommend('implementation')

        # Should detect error loop and recommend escalation
        assert recommendation['strategy'] in ['escalate', 'ask']
        assert recommendation['confidence'] > 0.5

        # Record decision and outcome
        decision_id = 'dec_001'
        engine.record_decision(decision_id, recommendation['strategy'], {
            'stage': 'implementation',
            'pattern': recommendation.get('pattern')
        })

        # Simulate successful intervention
        engine.record_outcome(decision_id, success=True, feedback='User resolved the issue')

        # Check status
        status = engine.get_status()
        assert status['memory']['tracked_outcomes'] == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
