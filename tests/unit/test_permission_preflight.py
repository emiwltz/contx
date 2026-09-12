"""Permission checks never fall through to collection or prompt APIs."""

import json
from types import ModuleType

import pytest

from contx.daemon import entrypoint
from contx.daemon.permission_preflight import run_permission_preflight


@pytest.mark.parametrize(
    "screen,titles", [(False, False), (True, False), (False, True), (True, True)]
)
def test_preflight_only_calls_nonprompting_access_checks(screen, titles):
    calls = []
    modules = {name: ModuleType(name) for name in ("Quartz", "ApplicationServices")}

    def screen_check():
        calls.append("screen")
        return screen

    def title_check():
        calls.append("titles")
        return titles

    modules["Quartz"].CGPreflightScreenCaptureAccess = screen_check
    modules["ApplicationServices"].AXIsProcessTrusted = title_check
    output = []
    assert (
        run_permission_preflight(
            platform="darwin",
            module_loader=modules.__getitem__,
            write_output=output.append,
        )
        == 0
    )
    assert calls == ["screen", "titles"]
    assert json.loads("".join(output)) == dict(
        schema_version=1,
        screen_recording=screen,
        accessibility=titles,
        permissions_requested=False,
        content_read=False,
    )


@pytest.mark.parametrize(
    "failure", ["platform", "missing_api", "exception", "invalid_boolean"]
)
def test_preflight_errors_do_not_report_access_or_expose_content(failure):
    output, errors = [], []
    module = ModuleType("Synthetic")

    def check():
        if failure == "exception":
            raise RuntimeError("synthetic private title")
        return 1

    if failure != "missing_api":
        module.CGPreflightScreenCaptureAccess = check
        module.AXIsProcessTrusted = check
    assert (
        run_permission_preflight(
            platform="linux" if failure == "platform" else "darwin",
            module_loader=lambda _: module,
            write_output=output.append,
            write_error=errors.append,
        )
        == 2
    )
    assert output == []
    assert errors == [
        "CONTX permission verification failed without collecting content.\n"
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--permission-preflight"],
        ["--permission-preflight", "extra"],
        ["--unknown"],
    ],
)
def test_entrypoint_dispatch_never_collects_for_diagnostic_or_unknown_arguments(
    monkeypatch, arguments
):
    calls = []
    monkeypatch.setattr(
        entrypoint, "run_collection_daemon", lambda: calls.append("collect") or 0
    )
    monkeypatch.setattr(
        entrypoint, "run_permission_preflight", lambda: calls.append("verify") or 2
    )
    result = entrypoint.run_entrypoint(arguments)
    if not arguments:
        assert result == 0 and calls == ["collect"]
    elif arguments == ["--permission-preflight"]:
        assert result == 2 and calls == ["verify"]
    else:
        assert result == 64 and calls == []


def test_full_module_preflight_does_not_initialize_runtime(tmp_path):
    import os
    import subprocess
    import sys

    code = """
import runpy, sys, types
quartz = types.ModuleType("Quartz")
quartz.CGPreflightScreenCaptureAccess = lambda: False
accessibility = types.ModuleType("ApplicationServices")
accessibility.AXIsProcessTrusted = lambda: False
sys.modules.update(Quartz=quartz, ApplicationServices=accessibility)
sys.argv = ["contx.daemon.entrypoint", "--permission-preflight"]
runpy.run_module("contx.daemon.entrypoint", run_name="__main__")
"""
    runtime = tmp_path / "must-not-exist"
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        env={**os.environ, "CONTX_RUNTIME_ROOT": str(runtime)},
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stderr == ""
    assert json.loads(result.stdout)["content_read"] is False
    assert not runtime.exists()
