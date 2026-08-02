"""Focused-window Accessibility probe tests without live title access."""

import pytest

from contx.collectors.macos import FocusedWindowTitleProbe
from contx.errors import CollectorUnavailableError


class FakeAccessibility:
    kAXFocusedApplicationAttribute = "focused-application"
    kAXFocusedWindowAttribute = "focused-window"
    kAXTitleAttribute = "title"
    kAXErrorSuccess = 0
    kAXErrorNoValue = -25212
    kAXErrorAttributeUnsupported = -25205

    def __init__(
        self,
        *,
        trusted: bool = True,
        title: object | None = "  Synthetic Project  ",
        fail_attribute: str | None = None,
    ) -> None:
        self.trusted = trusted
        self.title = title
        self.fail_attribute = fail_attribute
        self.calls: list[str] = []
        self.system = object()
        self.application = object()
        self.window = object()

    def AXIsProcessTrusted(self) -> bool:
        self.calls.append("preflight")
        return self.trusted

    def AXUIElementCreateSystemWide(self) -> object:
        self.calls.append("system")
        return self.system

    def AXUIElementCopyAttributeValue(
        self,
        element: object,
        attribute: str,
        value: None,
    ) -> tuple[int, object | None]:
        assert value is None
        self.calls.append(attribute)
        if attribute == self.fail_attribute:
            return (-25204, None)
        if attribute == self.kAXFocusedApplicationAttribute:
            assert element is self.system
            return (self.kAXErrorSuccess, self.application)
        if attribute == self.kAXFocusedWindowAttribute:
            assert element is self.application
            return (self.kAXErrorSuccess, self.window)
        assert attribute == self.kAXTitleAttribute
        assert element is self.window
        return (self.kAXErrorSuccess, self.title)


def test_missing_permission_prevents_every_accessibility_attribute_read() -> None:
    accessibility = FakeAccessibility(trusted=False)

    with pytest.raises(CollectorUnavailableError, match="permission is required"):
        FocusedWindowTitleProbe(accessibility).read()

    assert accessibility.calls == ["preflight"]


def test_authorized_title_is_read_and_normalized() -> None:
    accessibility = FakeAccessibility()

    title = FocusedWindowTitleProbe(accessibility).read()

    assert title == "Synthetic Project"
    assert accessibility.calls == [
        "preflight",
        "system",
        "focused-application",
        "focused-window",
        "title",
    ]


def test_absent_or_blank_title_is_not_an_error() -> None:
    assert FocusedWindowTitleProbe(FakeAccessibility(title=None)).read() is None
    assert FocusedWindowTitleProbe(FakeAccessibility(title="  ")).read() is None


def test_unexpected_accessibility_error_contains_no_title_data() -> None:
    with pytest.raises(CollectorUnavailableError, match="could not provide") as raised:
        FocusedWindowTitleProbe(
            FakeAccessibility(fail_attribute="focused-window")
        ).read()

    assert "Synthetic Project" not in str(raised.value)
