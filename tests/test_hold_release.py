"""
Tests for holding a job and releasing it later.

The interesting assertion is about WHEN the media guard runs. Submitting with
``hold`` deliberately skips it -- a label under review is exactly when the roll
is most likely to be swapped -- so the guard has to run at release instead. If
it ran only at hold time, several labels held against 50mm and released after a
change to 62mm would be silently discarded by the printer, which is the bug the
guard exists to prevent.
"""

import inspect

import pytest

from src.services.queue_service import PrintQueueService


@pytest.fixture
def queue():
    """A queue with no worker thread: jobs stay put so state can be inspected."""
    return PrintQueueService()


# --------------------------------------------------------------------------
# hold() records without enqueueing
# --------------------------------------------------------------------------

def test_held_job_is_not_queued(queue):
    job_id = queue.hold("text", "Sourdough", fn=lambda: None)
    assert queue.get(job_id)["status"] == "held"
    assert queue._queue.qsize() == 0


def test_held_job_with_executor_is_releasable(queue):
    job_id = queue.hold("text", "Sourdough", fn=lambda: None)
    assert queue.get(job_id)["can_release"] is True


def test_held_file_job_without_executor_is_not_releasable(queue):
    """An /image/compose upload is re-composed in the UI, not released."""
    job_id = queue.hold("image", "Homebox label", file_path="/tmp/x.png")
    assert queue.get(job_id)["can_release"] is False


def test_held_job_cannot_be_reprinted(queue):
    """Reprint repeats a finished job; a held one has never printed."""
    job_id = queue.hold("text", "Sourdough", fn=lambda: None)
    assert queue.get(job_id)["can_reprint"] is False


# --------------------------------------------------------------------------
# release() enqueues in place
# --------------------------------------------------------------------------

def test_release_enqueues_the_job(queue):
    job_id = queue.hold("text", "Sourdough", fn=lambda: None)
    queue.release(job_id)
    assert queue.get(job_id)["status"] == "queued"
    assert queue._queue.qsize() == 1


def test_release_keeps_the_same_job_id(queue):
    """A held job has never printed, so releasing it is that job happening."""
    job_id = queue.hold("text", "Sourdough", fn=lambda: None)
    assert queue.release(job_id) == job_id


def test_release_puts_the_original_executor_on_the_queue(queue):
    sentinel = lambda: "printed"
    job_id = queue.hold("text", "Sourdough", fn=sentinel)
    queue.release(job_id)
    queued_id, queued_fn = queue._queue.get_nowait()
    assert queued_id == job_id
    assert queued_fn is sentinel


def test_released_job_becomes_reprintable(queue):
    job_id = queue.hold("text", "Sourdough", fn=lambda: None)
    queue.release(job_id)
    assert queue.get(job_id)["can_reprint"] is True


# --------------------------------------------------------------------------
# release() refuses what it cannot do
# --------------------------------------------------------------------------

def test_release_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.release("nope")


def test_release_non_held_job_is_refused(queue):
    job_id = queue.submit("text", "Sourdough", lambda: None)
    with pytest.raises(ValueError, match="not held"):
        queue.release(job_id)


def test_release_file_only_job_is_refused(queue):
    """No executor to run -- the composer reopens these instead."""
    job_id = queue.hold("image", "Homebox label", file_path="/tmp/x.png")
    with pytest.raises(ValueError, match="composer"):
        queue.release(job_id)


def test_double_release_is_refused(queue):
    job_id = queue.hold("text", "Sourdough", fn=lambda: None)
    queue.release(job_id)
    with pytest.raises(ValueError, match="not held"):
        queue.release(job_id)


# --------------------------------------------------------------------------
# WHERE the guard runs -- the part that is easy to break later
# --------------------------------------------------------------------------

def _source_of(module_name, func_name):
    module = __import__(module_name, fromlist=[func_name])
    return inspect.getsource(getattr(module, func_name))


def test_release_endpoint_enforces_media_match():
    """The roll may have changed while the label sat under review."""
    src = _source_of("src.api.jobs_controller", "release_job")
    assert "enforce_media_match" in src


def test_hold_path_skips_the_guard_at_submit_time():
    """Holding is when the roll is most likely still to be changed.

    The guard must be conditional on holding, not removed: the immediate print
    path still needs it. Both live in the shared dispatch helper.
    """
    src = _source_of("src.utils.print_guard", "guard_and_dispatch")
    assert "if not holding:" in src
    assert "enforce_media_match(settings)" in src
