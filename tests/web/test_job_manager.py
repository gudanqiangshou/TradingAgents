import time
import threading
import pytest
from web.job_manager import JobManager, JobStatus, JobNotFoundError


def test_create_job_returns_id():
    mgr = JobManager()
    job_id = mgr.create_job()
    assert isinstance(job_id, str) and len(job_id) > 0


def test_new_job_is_pending():
    mgr = JobManager()
    job_id = mgr.create_job()
    assert mgr.get_status(job_id) == JobStatus.PENDING


def test_start_job_sets_running():
    mgr = JobManager()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    assert mgr.get_status(job_id) == JobStatus.RUNNING


def test_cannot_start_two_jobs():
    mgr = JobManager()
    job1 = mgr.create_job()
    job2 = mgr.create_job()
    mgr.start_job(job1)
    with pytest.raises(RuntimeError, match="already running"):
        mgr.start_job(job2)


def test_finish_job_sets_done():
    mgr = JobManager()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    mgr.finish_job(job_id)
    assert mgr.get_status(job_id) == JobStatus.DONE


def test_finish_job_releases_lock():
    mgr = JobManager()
    job1 = mgr.create_job()
    job2 = mgr.create_job()
    mgr.start_job(job1)
    mgr.finish_job(job1)
    mgr.start_job(job2)  # should not raise
    assert mgr.get_status(job2) == JobStatus.RUNNING


def test_error_job_releases_lock():
    mgr = JobManager()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    mgr.error_job(job_id, "something failed")
    assert mgr.get_status(job_id) == JobStatus.ERROR
    job2 = mgr.create_job()
    mgr.start_job(job2)  # lock released


def test_get_status_unknown_raises():
    mgr = JobManager()
    with pytest.raises(JobNotFoundError):
        mgr.get_status("no-such-id")


def test_has_running_job():
    mgr = JobManager()
    assert not mgr.has_running_job()
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    assert mgr.has_running_job()


def test_stop_event_set_on_watchdog_timeout():
    mgr = JobManager(watchdog_timeout=0.1)
    job_id = mgr.create_job()
    mgr.start_job(job_id)
    time.sleep(0.3)
    assert mgr.get_stop_event(job_id).is_set()
    assert mgr.get_status(job_id) == JobStatus.ERROR


def test_get_report_and_set_report():
    mgr = JobManager()
    job_id = mgr.create_job()
    assert mgr.get_report(job_id) is None
    mgr.set_report(job_id, "# Report content")
    assert mgr.get_report(job_id) == "# Report content"


def test_remove_job_discards_and_releases_lock():
    mgr = JobManager()
    job1 = mgr.create_job()
    mgr.start_job(job1)
    mgr.remove_job(job1)
    with pytest.raises(JobNotFoundError):
        mgr.get_status(job1)
    # lock released — a new job can start
    job2 = mgr.create_job()
    mgr.start_job(job2)
    assert mgr.has_running_job()


def test_remove_unknown_job_is_noop():
    mgr = JobManager()
    mgr.remove_job("no-such-id")  # must not raise
