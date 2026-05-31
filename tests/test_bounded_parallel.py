from __future__ import annotations

import threading
import unittest


class BoundedParallelTest(unittest.TestCase):
    def test_bounded_parallel_map_preserves_input_order_while_running_concurrently(self) -> None:
        from pipeline.decision.bounded_parallel import bounded_parallel_map

        barrier = threading.Barrier(2, timeout=3)
        active = 0
        max_active = 0
        lock = threading.Lock()

        def worker(value: int) -> str:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                if value in {1, 2}:
                    barrier.wait()
                return f"done-{value}"
            finally:
                with lock:
                    active -= 1

        result = bounded_parallel_map([1, 2, 3], worker, concurrency=2)

        self.assertEqual(result, ["done-1", "done-2", "done-3"])
        self.assertGreaterEqual(max_active, 2)

    def test_bounded_parallel_map_rejects_non_positive_concurrency(self) -> None:
        from pipeline.decision.bounded_parallel import bounded_parallel_map

        with self.assertRaises(ValueError):
            bounded_parallel_map([1], lambda value: value, concurrency=0)


if __name__ == "__main__":
    unittest.main()
