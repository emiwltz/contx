"""Synthetic capture authorization and dispatch boundaries."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contx.daemon import entrypoint, synthetic_capture
from contx.evaluation import macos_capture_smoke as smoke
from contx.macos_app.control import NativeHostControlStatus, NativeHostState

DISABLED = NativeHostControlStatus(1, NativeHostState.DISABLED, False, True, False)


@pytest.mark.parametrize(
    "host,status",
    [
        (False, DISABLED),
        (True, replace(DISABLED, background_enabled=True)),
        (True, replace(DISABLED, collector_running=True)),
        (True, replace(DISABLED, collection_paused=False)),
        (True, replace(DISABLED, state=NativeHostState.ACTIVE)),
    ],
)
def test_refuses_before_reading_content(monkeypatch, tmp_path, host, status, capsys):
    monkeypatch.setenv("CONTX_NATIVE_HOST", "1" if host else "0")
    monkeypatch.setattr(
        synthetic_capture.NativeHostControlService, "status", lambda _: status
    )

    def capture(_):
        pytest.fail("Unauthorized capture")

    monkeypatch.setattr(synthetic_capture, "run_focused_window_smoke", capture)
    assert synthetic_capture.run_synthetic_capture(tmp_path / "image.png") == 2
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().out == ""


def test_success_reports_only_synthetic_result(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONTX_NATIVE_HOST", "1")
    monkeypatch.setattr(
        synthetic_capture.NativeHostControlService, "status", lambda _: DISABLED
    )
    result = smoke.FocusedWindowSmokeResult(
        tmp_path / "image.png", 640, 400, "abc", "focused_window_changed"
    )
    monkeypatch.setattr(synthetic_capture, "run_focused_window_smoke", lambda _: result)
    assert synthetic_capture.run_synthetic_capture(result.output_path) == 0
    assert '"schema_version": 1' in capsys.readouterr().out


def test_child_failure_is_sanitized(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONTX_NATIVE_HOST", "1")

    def fail(_):
        raise RuntimeError("private window title")

    monkeypatch.setattr(synthetic_capture.NativeHostControlService, "status", fail)
    assert synthetic_capture.run_synthetic_capture(tmp_path / "image.png") == 2
    assert capsys.readouterr().err == "CONTX synthetic capture failed.\n"


def test_capture_dispatch_does_not_build_collector(monkeypatch):
    calls = []
    monkeypatch.setattr(
        synthetic_capture, "run_synthetic_capture", lambda path: calls.append(path) or 0
    )
    monkeypatch.setattr(
        entrypoint, "run_collection_daemon", lambda: pytest.fail("Collector started")
    )
    assert entrypoint.run_entrypoint(["--synthetic-capture", "/tmp/synthetic.png"]) == 0
    assert calls == [Path("/tmp/synthetic.png")]
    assert entrypoint.run_entrypoint(["--synthetic-capture"]) == 64
    assert (
        entrypoint.run_entrypoint(["--synthetic-capture", "/tmp/image.png", "extra"])
        == 64
    )


def test_foreign_process_rejected_before_title_read(monkeypatch):
    monkeypatch.setattr(smoke.os, "getpid", lambda: 123)
    application = SimpleNamespace(read=lambda: SimpleNamespace(process_id=456))
    with pytest.raises(
        smoke.CollectorUnavailableError, match="process is not foreground"
    ):
        smoke._sample_expected_window(
            expected_title=smoke.PRIMARY_TITLE,
            application=application,
            window=None,
            title=None,
        )
