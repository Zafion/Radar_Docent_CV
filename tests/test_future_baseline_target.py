import unittest
from scripts.reconcile_batch_trial import validate_trial_target

class FutureBaselineTargetTests(unittest.TestCase):
    def test_only_explicit_isolated_trial_name_is_allowed(self):
        validate_trial_target('baseline_future_trial',True)
        for target in ('postgres','production','baseline','baseline_future_trial_extra'):
            with self.assertRaises(ValueError):validate_trial_target(target,True)
        with self.assertRaises(ValueError):validate_trial_target('baseline_future_trial',False)
