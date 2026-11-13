"""Running per-space API work in parallel, one client per worker thread."""

import threading
import time

from google_chat_mcp.concurrency import parallel_map


def test_results_keep_input_order_and_each_thread_builds_one_client():
    made = []
    lock = threading.Lock()

    def factory():
        with lock:
            made.append(threading.get_ident())
        return object()

    def work(client, n):
        time.sleep(0.01 * (5 - n % 5))  # finish out of order
        return (n, client)

    results = parallel_map(work, range(20), chat=None, factory=factory, workers=4)
    assert [n for n, _ in results] == list(range(20))
    assert 1 <= len(made) <= 4
    assert len(set(made)) == len(made)  # never two clients for one thread


def test_without_a_factory_runs_in_order_on_the_given_client():
    sentinel = object()
    assert parallel_map(lambda c, n: (c is sentinel, n), [1, 2, 3], chat=sentinel) == [
        (True, 1),
        (True, 2),
        (True, 3),
    ]
