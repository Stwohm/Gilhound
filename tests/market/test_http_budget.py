import io
import subprocess
import sys
import urllib.error
import urllib.request
from email.utils import formatdate
from types import SimpleNamespace

import pytest

from gilhound.market import budget, http
from gilhound.market.budget import HostBudget

# Hold every connection in a separate interpreter. Exiting without unwinding
# the stack exercises OS lock reclamation after abrupt process termination.
HOLD_SLOTS = """
from contextlib import ExitStack
from pathlib import Path
import sys
from gilhound.market.budget import HostBudget
budget = HostBudget('example.invalid', Path(sys.argv[1]))
stack = ExitStack()
for i in range(8):
    stack.enter_context(budget.connection())
print('ready', flush=True)
sys.stdin.readline()
"""


def test_process_shared_connections_and_start_budget(tmp_path, monkeypatch):
    child = subprocess.Popen(
        [sys.executable, "-c", HOLD_SLOTS, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        peer = HostBudget("example.invalid", tmp_path)
        # Freeze the clock at the last admission to prove the child also
        # reserved the next start across the process boundary.
        last_start = peer._next() - 1 / 25
        monkeypatch.setattr(budget, "wall_time", lambda: last_start)
        waits = []

        def stop_wait(delay):
            waits.append(delay)
            raise TimeoutError

        monkeypatch.setattr(budget, "wait", stop_wait)
        with pytest.raises(TimeoutError), peer.connection():
            pytest.fail("start budget was bypassed")
        assert waits[-1] == pytest.approx(0.04, abs=0.00001)
        monkeypatch.setattr(budget, "wall_time", lambda: last_start + 1)
        with pytest.raises(TimeoutError), peer.connection():
            pytest.fail("ninth connection was admitted")
        with HostBudget("other.invalid", tmp_path).connection():
            pass
    finally:
        child.kill()
        child.communicate(timeout=5)
    with peer.connection():
        pass


@pytest.mark.parametrize(
    ("header", "delay"),
    [("60", 60), (formatdate(1_800_000_060, usegmt=True), 60), ("invalid", 2), (None, 2)],
)
def test_retry_after_shared_with_next_client(tmp_path, monkeypatch, header, delay):
    now = [1_800_000_000.0]
    timer = SimpleNamespace(time=lambda: now[0])
    monkeypatch.setattr(budget, "wall_time", timer.time)
    monkeypatch.setattr(http, "time", timer)
    monkeypatch.setattr(budget, "wait", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls = []

    def respond(req, **kwargs):
        calls.append(now[0])
        if len(calls) == 1:
            headers = {"Retry-After": header} if header else {}
            raise urllib.error.HTTPError(req.full_url, 429, "", headers, None)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    with pytest.raises(urllib.error.HTTPError):
        http.HttpClient(attempts=1).request("https://example.invalid/a")
    assert http.HttpClient().request("https://example.invalid/b") == {}
    assert calls[1] - calls[0] == delay
