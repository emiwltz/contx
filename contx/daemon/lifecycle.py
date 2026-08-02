"""Single-process lifecycle for native controls and audited collection."""

from __future__ import annotations

from typing import Never, Protocol

from contx.application import ContinuousCollectionResult


class ProcessLease(Protocol):
    def acquire(self) -> None: ...

    def release(self) -> None: ...


class NativeMonitor(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class NativeMenu(Protocol):
    def start(self) -> None: ...

    def refresh(self) -> object: ...

    def stop(self) -> None: ...


class AuditedCollectionSession(Protocol):
    @property
    def is_running(self) -> bool: ...

    def start(self) -> None: ...

    def tick(self) -> int: ...

    def stop(self) -> ContinuousCollectionResult: ...

    def abort(self, error: Exception) -> Never: ...


class CollectionDaemonLifecycle:
    """Coordinate one lease, native surfaces, and an audited collection run."""

    def __init__(
        self,
        *,
        lease: ProcessLease,
        notifications: NativeMonitor,
        menu: NativeMenu,
        session: AuditedCollectionSession,
    ) -> None:
        self._lease = lease
        self._notifications = notifications
        self._menu = menu
        self._session = session
        self._lease_held = False
        self._notifications_started = False
        self._menu_started = False
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._started:
            raise RuntimeError("collection daemon lifecycle is already started")
        try:
            self._lease.acquire()
            self._lease_held = True
            self._notifications.start()
            self._notifications_started = True
            self._menu.start()
            self._menu_started = True
            self._session.start()
        except Exception as error:
            cleanup_failed = self._release_native_resources()
            if cleanup_failed:
                error.add_note("CONTX daemon startup cleanup was incomplete")
            raise
        self._started = True

    def tick(self) -> int:
        if not self._started:
            raise RuntimeError("collection daemon lifecycle is not started")
        try:
            persisted = self._session.tick()
            self._menu.refresh()
            return persisted
        except Exception as error:
            if self._session.is_running:
                self._session.abort(error)
            raise

    def stop(self) -> ContinuousCollectionResult | None:
        if not self._started:
            return None
        self._started = False
        result: ContinuousCollectionResult | None = None
        first_error: Exception | None = None
        if self._session.is_running:
            try:
                result = self._session.stop()
            except Exception as error:
                first_error = error
        cleanup_failed = self._release_native_resources()
        if cleanup_failed:
            if first_error is None:
                raise RuntimeError("CONTX daemon cleanup was incomplete")
            first_error.add_note("CONTX daemon cleanup was incomplete")
        if first_error is not None:
            raise first_error
        return result

    def _release_native_resources(self) -> bool:
        cleanup_failed = False
        if self._menu_started:
            self._menu_started = False
            try:
                self._menu.stop()
            except Exception:
                cleanup_failed = True
        if self._notifications_started:
            self._notifications_started = False
            try:
                self._notifications.stop()
            except Exception:
                cleanup_failed = True
        if self._lease_held:
            self._lease_held = False
            try:
                self._lease.release()
            except Exception:
                cleanup_failed = True
        return cleanup_failed
