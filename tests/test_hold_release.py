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


def test_every_submission_endpoint_accepts_hold():
    """Holding must be uniform.

    A caller should not have to remember which endpoints can hold and which
    cannot; that is how the pause-the-whole-queue workaround came about.
    """
    import yaml

    spec = yaml.safe_load(open("src/api/openapi.yaml"))
    submission_paths = [
        "/text/print", "/qrcode/print", "/label/text-qrcode",
        "/label/text-image", "/pdf/print", "/image/print",
    ]
    missing = []
    for path in submission_paths:
        body = spec["paths"][path]["post"]["requestBody"]["content"]
        schema = next(iter(body.values()))["schema"]
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            schema = spec["components"]["schemas"][name]
        if "hold" not in schema.get("properties", {}):
            missing.append(path)
    assert not missing, f"endpoints missing the hold flag: {missing}"


def test_openapi_has_no_duplicate_keys():
    """safe_load silently keeps the last of a duplicated key.

    A second `hold:` added to a schema by a bulk edit would parse fine and
    quietly shadow the first, so check explicitly.
    """
    import yaml

    class Strict(yaml.SafeLoader):
        pass

    def no_dupes(loader, node, deep=False):
        seen = set()
        mapping = {}
        for k, v in node.value:
            key = loader.construct_object(k, deep=deep)
            assert key not in seen, (
                f"duplicate key {key!r} at line {k.start_mark.line + 1}")
            seen.add(key)
            mapping[key] = loader.construct_object(v, deep=deep)
        return mapping

    Strict.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_dupes)
    with open("src/api/openapi.yaml") as fh:
        yaml.load(fh, Loader=Strict)


def test_hold_path_skips_the_guard_at_submit_time():
    """Holding is when the roll is most likely still to be changed.

    The guard must be conditional on holding, not removed: the immediate print
    path still needs it. Both live in the shared dispatch helper.
    """
    src = _source_of("src.utils.print_guard", "guard_and_dispatch")
    assert "if not holding:" in src
    assert "enforce_media_match(settings)" in src


# --------------------------------------------------------------------------
# amend(): the review loop
# --------------------------------------------------------------------------

def test_amend_replaces_content_in_place(queue):
    job_id = queue.hold("text", "Old", params={"text": "old"}, fn=lambda: None)
    queue.amend(job_id, "New", lambda: None, params={"text": "new"})
    job = queue.get(job_id)
    assert job["label"] == "New"
    assert job["params"]["text"] == "new"


def test_amend_keeps_the_job_held(queue):
    """Amending is editing, not printing."""
    job_id = queue.hold("text", "Old", fn=lambda: None)
    queue.amend(job_id, "New", lambda: None)
    assert queue.get(job_id)["status"] == "held"
    assert queue._queue.qsize() == 0


def test_amend_keeps_the_same_id(queue):
    job_id = queue.hold("text", "Old", fn=lambda: None)
    assert queue.amend(job_id, "New", lambda: None) == job_id


def test_amend_swaps_the_executor(queue):
    """Otherwise releasing would print the pre-edit label."""
    job_id = queue.hold("text", "Old", fn=lambda: "old")
    new_fn = lambda: "new"
    queue.amend(job_id, "New", new_fn)
    queue.release(job_id)
    _, queued_fn = queue._queue.get_nowait()
    assert queued_fn is new_fn


def test_amend_refuses_a_queued_job(queue):
    """It is already on its way to the printer."""
    job_id = queue.submit("text", "x", lambda: None)
    with pytest.raises(ValueError, match="not held"):
        queue.amend(job_id, "New", lambda: None)


def test_amend_refuses_an_unknown_job(queue):
    with pytest.raises(KeyError):
        queue.amend("nope", "New", lambda: None)


def test_amend_does_not_change_the_type(queue):
    """A caller may already hold params shaped by the original type."""
    job_id = queue.hold("text", "Old", fn=lambda: None)
    queue.amend(job_id, "New", lambda: None)
    assert queue.get(job_id)["type"] == "text"


def test_amend_keeps_the_file_when_none_is_given(queue):
    """A text job amended from a text job never had one; do not clear it."""
    job_id = queue.hold("image", "Old", file_path="/tmp/a.png", fn=lambda: None)
    queue.amend(job_id, "New", lambda: None)
    assert queue.get_file_path(job_id) == "/tmp/a.png"
