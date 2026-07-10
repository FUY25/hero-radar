from __future__ import annotations

import threading
import unittest


class StableBoundedParallelMapTest(unittest.TestCase):
    def test_returns_results_in_input_order_when_workers_finish_out_of_order(self) -> None:
        from pipeline.source_concurrency import stable_bounded_parallel_map

        release_first = threading.Event()
        second_started = threading.Event()

        def worker(value: int) -> int:
            if value == 1:
                self.assertTrue(second_started.wait(timeout=1))
                release_first.wait(timeout=1)
            else:
                second_started.set()
                release_first.set()
            return value * 10

        self.assertEqual(
            stable_bounded_parallel_map([1, 2], worker, concurrency=2),
            [10, 20],
        )

    def test_never_exceeds_the_configured_concurrency(self) -> None:
        from pipeline.source_concurrency import stable_bounded_parallel_map

        lock = threading.Lock()
        active = 0
        peak = 0
        two_running = threading.Event()
        release = threading.Event()

        def worker(value: int) -> int:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    two_running.set()
            if value < 2:
                self.assertTrue(two_running.wait(timeout=1))
                release.wait(timeout=1)
            with lock:
                active -= 1
            return value

        timer = threading.Timer(0.05, release.set)
        timer.start()
        try:
            self.assertEqual(
                stable_bounded_parallel_map(range(4), worker, concurrency=2),
                [0, 1, 2, 3],
            )
        finally:
            timer.cancel()

        self.assertEqual(peak, 2)


class RateGateTest(unittest.TestCase):
    def test_spaces_request_starts_with_an_injectable_clock(self) -> None:
        from pipeline.source_concurrency import RateGate

        now = [10.0]
        sleeps: list[float] = []

        def clock() -> float:
            return now[0]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        gate = RateGate(
            max_in_flight=1,
            min_interval_seconds=0.5,
            clock=clock,
            sleeper=sleep,
        )
        starts: list[float] = []

        gate.run(lambda: starts.append(clock()))
        gate.run(lambda: starts.append(clock()))
        gate.run(lambda: starts.append(clock()))

        self.assertEqual(starts, [10.0, 10.5, 11.0])
        self.assertEqual(sleeps, [0.5, 0.5])

    def test_releases_slot_when_request_raises(self) -> None:
        from pipeline.source_concurrency import RateGate

        gate = RateGate(max_in_flight=1)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            gate.run(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        self.assertEqual(gate.run(lambda: "ok"), "ok")


class RequestPolicyRegistryTest(unittest.TestCase):
    def test_reuses_one_gate_for_every_url_on_the_same_host(self) -> None:
        from pipeline.source_concurrency import RequestPolicyRegistry

        created: list[tuple[int, float]] = []

        class RecordingGate:
            def __init__(self, *, max_in_flight: int, min_interval_seconds: float) -> None:
                created.append((max_in_flight, min_interval_seconds))

            def run(self, function):
                return function()

        policies = RequestPolicyRegistry(
            gate_factory=RecordingGate,
        )

        policies.register_url(
            "https://api.github.com/search/repositories?q=first",
            max_in_flight=3,
            min_interval_seconds=6.2,
        )
        policies.run_url(
            "https://api.github.com/search/repositories?q=second",
            lambda: None,
            max_in_flight=3,
            min_interval_seconds=6.2,
        )

        self.assertEqual(created, [(3, 6.2)])

    def test_different_hosts_use_separate_rate_buckets(self) -> None:
        from pipeline.source_concurrency import RequestPolicyRegistry

        policies = RequestPolicyRegistry()
        github_html = policies.register_url(
            "https://github.com/trending",
            max_in_flight=4,
            min_interval_seconds=0.5,
        )
        github_api = policies.register_url(
            "https://api.github.com/search/repositories",
            max_in_flight=3,
            min_interval_seconds=6.2,
        )

        self.assertIsNot(github_html, github_api)
        self.assertEqual(policies.spec_for_url("https://github.com/other"), (4, 0.5))
        self.assertEqual(policies.spec_for_url("https://api.github.com/rate_limit"), (3, 6.2))


if __name__ == "__main__":
    unittest.main()
