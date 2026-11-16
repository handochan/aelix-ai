"""Ctrl+B: a running foreground delegation moves into a background job (#775, ADR-0298).

WHAT MOVES. A TUI ``agent(action="run")`` with one task, or a ``resume``, runs
inside the turn task as it always has: ``spawn_granted``/``resume_granted`` →
``_run`` → ``PrintChannel.run`` → ``_stream_and_reap``. The call site awaits
that coroutine through :func:`drive`, a PEP 380 delegate that hands every
``send`` and ``throw`` to it in the step the task delivers it. While the child
is live, ``_stream_and_reap``'s wait also waits on the row's detach future; the
key resolves it, the run awaits :func:`migrate_point`, and the delegate runs
:meth:`CallDetach.commit` synchronously. A commit that publishes returns
:class:`Detached`: the tool answers with the detach text in that same step, and
the job's own task resumes THE SAME coroutine from that point. A refusal sends
``False`` back and the run goes on where it was.

A CALL THAT IS NEVER DETACHED is driven as ``await`` drives it: the delegate
adds no task, no future and no callback, and the detach future is one more
member of an ``asyncio.wait`` that never completes. That is claimed for the
measured cases only (``tests/agents_ext/test_detach_driver_611.py``).

THE CROSSING SET. Moving a suspended coroutine to another task is safe only
when no frame suspended across the point holds state bound to the task it ran
in. :func:`audit` admits a function only when no ``with`` encloses its
crossing await, the await calls the next function directly, and it names no
timeout, ``TaskGroup``, ``current_task``, ``ContextVar`` or ``copy_context``;
the product's five are admitted once (:func:`product_allowlist`), and every
commit walks the suspended chain (:meth:`Allowlist.chain_allowed`) and refuses
a frame that is not one of them. :func:`migrate_point` yields its sentinel
only inside a task that is driving through :func:`drive`, so a wrapper that
ran part of the chain in a task of its own can never hand it to a bare task.

THE ORDER OF A COMMIT (ADR-0298 §3): validate; create the job's task, gated
and not started eagerly — the one step whose failure cannot be undone, so it
comes first; publish, each step with its undo; take the slot out of the call's
reservation last; open the gate. A job task cancelled before its first step is
rescued by its done-callback: the moved run takes its own cancel path through
the job service's ``_drive``, and the job ends ``stopped`` and frees its slot.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import contextvars
import functools
import inspect
import logging
import textwrap
import types
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aelix_agents.print_channel import RunningChild

logger = logging.getLogger(__name__)

MIGRATE = object()
"""The sentinel :func:`migrate_point` yields to :func:`drive`."""

_DRIVING: dict[Any, int] = {}
"""Tasks running inside :func:`drive`, with how deep."""


@types.coroutine
def migrate_point() -> Any:
    """The run's one safe point: ``True`` once it runs in the job's task, ``False`` when it stays.

    Yields :data:`MIGRATE` only when the current task is driving through
    :func:`drive`; anywhere else it returns ``False`` at once.
    """

    if _DRIVING.get(asyncio.current_task(), 0) < 1:
        return False
    return (yield MIGRATE)


def _commit_or_refuse(commit: Callable[[Any], Any] | None, inner: Any) -> Any:
    if commit is None:
        return None
    try:
        return commit(inner)
    except Exception:  # noqa: BLE001 — a commit that raises is a refusal
        logger.debug("the detach commit raised; the run stays", exc_info=True)
        return None


def _undrive(task: Any) -> None:
    left = _DRIVING.get(task, 1) - 1
    if left:
        _DRIVING[task] = left
    else:
        _DRIVING.pop(task, None)


@types.coroutine
def drive(
    inner: Coroutine[Any, Any, Any],
    commit: Callable[[Any], Any] | None = None,
    *,
    first: Any = None,
    first_exc: BaseException | None = None,
) -> Any:
    """Await ``inner`` as ``await`` would; at :data:`MIGRATE`, ``commit(inner)`` decides.

    ``first``/``first_exc`` is the first move: ``True`` resumes a run parked at
    :func:`migrate_point` in the job's task, a ``CancelledError`` throws its
    cancel in (the rescue).
    """

    task = asyncio.current_task()
    _DRIVING[task] = _DRIVING.get(task, 0) + 1
    try:
        value, exc = first, first_exc
        while True:
            try:
                yielded = inner.throw(exc) if exc is not None else inner.send(value)
            except StopIteration as stop:
                return stop.value
            if yielded is MIGRATE:
                outcome = _commit_or_refuse(commit, inner)
                if outcome is not None:
                    return outcome
                value, exc = False, None
                continue
            try:
                value, exc = (yield yielded), None
            except GeneratorExit:
                inner.close()
                raise
            except BaseException as err:  # noqa: BLE001 — thrown into ``inner`` in this step
                value, exc = None, err
    finally:
        _undrive(task)


# === the crossing set ==================================================================

_FORBIDDEN = frozenset(
    {"timeout", "timeout_at", "TaskGroup", "current_task", "ContextVar", "copy_context"}
)
_WRAPPERS = frozenset({"wait_for", "shield", "ensure_future", "create_task", "gather", "wait"})


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", None)


def _names_forbidden(body: ast.AST, *, strict: bool = True) -> str | None:
    """A task- or context-bound construct named in ``body``, imported under any name too.

    ``strict`` (the crossing function itself) refuses the bare name
    ``timeout`` as well; a helper's (``strict=False``) may have a parameter of
    that name, and only ``asyncio.timeout`` refuses there.
    """

    banned = _FORBIDDEN if strict else _FORBIDDEN - {"timeout"}
    for node in ast.walk(body):
        why = _node_refusal(node, banned)
        if why is not None:
            return why
    return None


def _node_refusal(node: ast.AST, banned: frozenset[str]) -> str | None:
    if isinstance(node, ast.alias) and node.name.rsplit(".", 1)[-1] in _FORBIDDEN:
        return f"imports {node.name}"
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    if isinstance(node, (ast.Attribute, ast.Name)) and name in banned:
        return f"names {name}"
    return "names asyncio.timeout" if _is_asyncio_timeout(node) else None


def _is_asyncio_timeout(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "timeout"
        and isinstance(node.value, ast.Name)
        and node.value.id == "asyncio"
    )


@functools.cache
def _forbidden_objects() -> dict[int, str]:
    """The task- and context-bound callables themselves, by identity: an alias is the same object."""

    import contextvars as cv

    found = {
        name: getattr(asyncio, name, None)
        for name in ("current_task", "timeout", "timeout_at", "TaskGroup")
    }
    found.update(ContextVar=cv.ContextVar, copy_context=cv.copy_context)
    return {id(obj): name for name, obj in found.items() if obj is not None}


def _code_names(code: types.CodeType) -> set[str]:
    """Every global name ``code`` and the code nested in it (comprehensions, lambdas) reads."""

    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


def _refers_to_forbidden(fn: Any) -> str | None:
    """A global or a closure cell of ``fn`` that is a ContextVar or a task-bound callable, under any name."""

    import contextvars as cv

    banned = _forbidden_objects()
    values = [(name, fn.__globals__.get(name)) for name in sorted(_code_names(fn.__code__))]
    for cell in fn.__closure__ or ():
        with contextlib.suppress(ValueError):
            values.append(("a closure cell", cell.cell_contents))
    for name, value in values:
        if isinstance(value, cv.ContextVar):
            return f"reaches the ContextVar {name}"
        if id(value) in banned:
            return f"{name} is {banned[id(value)]}"
    return None


def _helper_refusal(fn: Any, *, depth: int = 3) -> str | None:
    """A plain function of ``fn``'s own module that ``fn`` calls, at most ``depth`` calls deep,
    which reaches a task- or context-bound construct (Codex r1 5): a token it hands back can be
    held across the point by the crossing function even though the helper returned."""

    seen: set[types.CodeType] = {fn.__code__}
    frontier = [fn]
    for _ in range(depth):
        called = []
        for caller in frontier:
            for name in sorted(_code_names(caller.__code__)):
                value = caller.__globals__.get(name)
                if _same_module_function(value, fn) and value.__code__ not in seen:
                    seen.add(value.__code__)
                    called.append(value)
        for helper in called:
            why = _refers_to_forbidden(helper) or _helper_source_refusal(helper)
            if why is not None:
                return f"its helper {helper.__name__} {why}"
        frontier = called
    return None


def _same_module_function(value: Any, fn: Any) -> bool:
    return isinstance(value, types.FunctionType) and value.__module__ == fn.__module__


def _helper_source_refusal(helper: Any) -> str | None:
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(helper)))
    except (OSError, TypeError, SyntaxError):
        return None
    return _names_forbidden(tree, strict=False)


class _CrossingVisitor:
    """Finds the crossing awaits of one function body and what encloses or wraps them."""

    def __init__(self, crossing: frozenset[str], body: ast.AST) -> None:
        self.crossing = crossing
        self.body = body
        self.found = 0
        self.bad: str | None = None

    def visit(self, node: ast.AST, guarded: bool) -> None:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            guarded = True
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            self._await(node.value, guarded)
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                self.visit(child, guarded)

    def _await(self, call: ast.Call, guarded: bool) -> None:
        name = _callee_name(call)
        if name in self.crossing:
            self.found += 1
            if guarded:
                self.bad = f"a with-block encloses the crossing await of {name}"
        if name in _WRAPPERS and any(
            isinstance(a, ast.Call) and _callee_name(a) in self.crossing for a in call.args
        ):
            self.bad = f"{name} wraps the crossing call"


def audit(fn: Any, crossing: set[str] | frozenset[str]) -> tuple[bool, str]:
    """``(admitted, why)``: may ``fn`` be suspended across the point at an await of ``crossing``?"""

    fn = inspect.unwrap(fn)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError) as exc:
        return False, f"no source: {exc}"
    body = tree.body[0]
    why = _names_forbidden(body) or _refers_to_forbidden(fn) or _helper_refusal(fn)
    if why is not None:
        return False, why
    visitor = _CrossingVisitor(frozenset(crossing), body)
    for child in getattr(body, "body", ()):
        visitor.visit(child, False)
    if visitor.bad is not None:
        return False, visitor.bad
    if not visitor.found:
        return False, "no crossing await"
    return True, "ok"


class Allowlist:
    """The audited functions a run may be suspended in across :func:`migrate_point`."""

    def __init__(self) -> None:
        self.codes: set[types.CodeType] = set()
        self.refused: dict[str, str] = {}

    def admit(self, fn: Any, crossing: set[str]) -> bool:
        ok, why = audit(fn, crossing)
        if ok:
            self.codes.add(inspect.unwrap(fn).__code__)
        else:
            self.refused[getattr(fn, "__qualname__", repr(fn))] = why
        return ok

    def chain_allowed(self, inner: Any) -> bool:
        """Every frame from ``inner`` down to :func:`migrate_point` is admitted (walked at the commit)."""

        node, depth = inner, 0
        while node is not None:
            code = getattr(node, "cr_code", None) or getattr(node, "gi_code", None)
            if code is _MIGRATE_CODE:
                return depth > 0
            if code not in self.codes:
                return False
            node = node.cr_await if hasattr(node, "cr_await") else getattr(node, "gi_yieldfrom", None)
            depth += 1
        return False


_MIGRATE_CODE = migrate_point.__code__


@functools.cache
def product_allowlist() -> Allowlist:
    """The five crossing functions of the product chain, audited once (ADR-0298 §2)."""

    from aelix_agents.print_channel import PrintChannel
    from aelix_agents.runtime import _SubagentRuntimeImpl

    allow = Allowlist()
    for fn, crossing in (
        (_SubagentRuntimeImpl.spawn_granted, {"_run"}),
        (_SubagentRuntimeImpl.resume_granted, {"_run"}),
        (_SubagentRuntimeImpl._run, {"run"}),
        (PrintChannel.run, {"_stream_and_reap"}),
        (PrintChannel._stream_and_reap, {"migrate_point"}),
    ):
        allow.admit(fn, crossing)
    return allow


# === the moved run's gate and rescue ======================================================


@dataclass
class Box:
    """The moved coroutine, held apart from the job's task; ``published`` is the gate.

    ``entered`` is set by the runner in the step it starts driving ``inner``
    (never earlier): until then the job's first task may still end without
    the run, and its done-callback rescues it (:func:`_rescue`).
    """

    inner: Any
    published: bool = False
    entered: bool = False
    task: asyncio.Task[Any] | None = None


async def _entry(box: Box, start: Callable[[], Coroutine[Any, Any, Any]]) -> None:
    """The job's task: returns at once unless the commit published (``box.published``)."""

    if not box.published:
        return
    await start()


def _rescue(task: asyncio.Task[Any], *, box: Box, rescue: Callable[[], None]) -> None:
    """The job's first task ended and no runner took the run: run the moved run's own cancel path.

    That is a cancel before the task's first step (Codex r3 M2), or a startup
    step that raised before the runner drove ``inner`` (Codex r1 2). Either way
    the run is still parked at its point, and nothing else will resume it.
    """

    if not box.published or box.entered:
        return
    _retrieve(task)
    try:
        rescue()
    except Exception:  # noqa: BLE001 — a stated limit: the row and its records are lost
        logger.warning("a delegation moved to the background could not be rescued", exc_info=True)


def _retrieve(task: asyncio.Task[Any]) -> None:
    """The ended task's failure, read and logged once: the rescue answers for it, not a GC-time line.

    A cancel is no failure: ``exception()`` raises it, and nothing is logged.
    """

    with contextlib.suppress(BaseException):
        failure = task.exception()
        logger.warning(
            "a moved run's job task ended before its runner took the run: %r; rescuing it",
            failure,
            exc_info=failure,
        )


# === one call's side of Ctrl+B ==============================================================


@dataclass
class Detached:
    """What a published commit hands the call: the job, and the text the tool answers with."""

    job_id: str
    agent_id: str
    text: str


@dataclass
class AdoptedLink:
    """``RunningChild.adopted``: where ``_run`` hands its closing records once the run moved."""

    job_id: str
    records: list[tuple[str, dict[str, Any]]] | None = None

    def hand(self, records: list[tuple[str, dict[str, Any]]]) -> None:
        self.records = list(records)


def detach_text(profile: str, *, posture: str, agent_id: str, job_id: str, resumed: bool) -> str:
    """What the model reads when its call moved to the background (design proposal, contract r3 §2.6)."""

    from aelix_agents.tool import call_literal

    who = "the resumed agent" if resumed else "agent"
    return (
        f"Moved to the background at the user's request: {who} {call_literal(profile)} "
        f"keeps running at {posture} (id {agent_id}; its job is {job_id}). Do not wait, "
        "sleep or poll for it: its result comes back to you once, as a system notice, when "
        'it finishes. Go on with other work. agent(action="status", '
        f'id="{agent_id}") shows how it is doing.'
    )


@dataclass
class CallDetach:
    """One detachable call: its offer while the child is live, its ONE attempt, and the commit.

    States (ADR-0298 §2): ``idle`` until the run parks in the live wait;
    ``parked`` with the offer made; ``claimed`` once the run woke for the key
    with the child still live; then ``adopted`` or ``refused``. ``left`` is a
    live wait that ended without a request. After ``refused`` the call offers
    nothing again.

    ``adopt`` builds and publishes the job (the extension's, which knows the
    kind); it is called inside :meth:`commit` with the ordered steps below.
    """

    adopt: Callable[[CallDetach, Any], Detached | None]
    offer: Callable[[CallDetach], Callable[[], None]]
    refused_note: Callable[[str], None] | None = None
    state: str = "idle"
    row: RunningChild | None = None
    released: bool = False
    refusal: str | None = None
    job_id: str | None = None
    _future: asyncio.Future[bool] | None = field(default=None, repr=False)
    _withdraw: Callable[[], None] | None = field(default=None, repr=False)

    # ── the per-call progress tap ──────────────────────────────────

    def guard(self, fn: Callable[[Any], None]) -> Callable[[Any], None]:
        """``fn`` until the commit releases the call; nothing after it."""

        def tap(progress: Any) -> None:
            if not self.released:
                fn(progress)

        return tap

    # ── the live wait (``_stream_and_reap``) ───────────────────────

    def park(self, row: RunningChild) -> asyncio.Future[bool] | None:
        """The run is in its live wait: make the offer; the future the key resolves. Total."""

        if self.state != "idle":
            return None
        try:
            self._future = asyncio.get_running_loop().create_future()
            self.row = row
            self._withdraw = self.offer(self)
        except Exception:  # noqa: BLE001 — no offer, the run goes on as today
            logger.debug("no detach offer for %s", row.id, exc_info=True)
            self.state = "refused"
            return None
        self.state = "parked"
        return self._future

    def claim(self) -> None:
        """The run woke for the key with the child still live: withdraw the offer."""

        self.state = "claimed"
        self._take_back()

    def leave(self) -> None:
        """The live wait ended (an end, a cancel): no offer from here on. Total, idempotent."""

        if self.state == "parked":
            self.state = "left"
        self._take_back()

    def _take_back(self) -> None:
        withdraw, self._withdraw = self._withdraw, None
        if withdraw is not None:
            with contextlib.suppress(Exception):
                withdraw()

    def ready(self) -> bool:
        """Whether a Ctrl+B now would be taken: parked, not yet asked, the child live and not stopping."""

        row, future = self.row, self._future
        return (
            self.state == "parked"
            and future is not None
            and not future.done()
            and row is not None
            and row.proc is not None
            and row.proc.returncode is None
            and not row.stopped
            and not row.eager_kill
        )

    def request(self) -> bool:
        """The key: wake the run; whether it was taken."""

        if not self.ready():
            return False
        assert self._future is not None
        self._future.set_result(True)
        return True

    # ── the commit, inside ``drive`` ───────────────────────────────

    def commit(self, inner: Any) -> Detached | None:
        """One attempt: the ordered commit, or ``None`` — the run stays, nothing touched."""

        if self.state != "claimed":
            return None
        self.state = "refused"
        try:
            detached = self.adopt(self, inner)
        except Exception as exc:  # noqa: BLE001 — a commit that raises is a refusal
            logger.debug("the detach commit raised", exc_info=True)
            detached = None
            self.refusal = self.refusal or f"{type(exc).__name__}: {exc}"
        if detached is None:
            self._note()
            return None
        self.state = "adopted"
        return detached

    def _note(self) -> None:
        note = self.refused_note
        if note is not None:
            with contextlib.suppress(Exception):
                note(self.refusal or "it could not be moved now")


@dataclass
class Plan:
    """What a commit publishes, built without side effects once the checks passed.

    ``start`` is the job task's body (behind the gate), ``rescue`` runs when
    that task ends before its runner took the run, ``publish`` is each step
    with its undo, in order, ``take_slot`` moves the call's slot into the job,
    ``result`` is what the call returns once the gate opened — built here, so
    nothing that can fail is left after the move (r4 §2.4 step 2) — and
    ``announce`` tells the job's listeners, after the gate opened and never
    inside the transaction (Codex r1 3).
    """

    start: Callable[[], Coroutine[Any, Any, Any]]
    rescue: Callable[[], None]
    publish: list[tuple[Callable[[], Any], Callable[[], Any]]]
    take_slot: Callable[[], None]
    result: Any
    announce: Callable[[], None] = lambda: None


def run_ordered_commit(
    inner: Any,
    *,
    checks: list[Callable[[], str | None]],
    build: Callable[[Box], Plan],
    on_refused: Callable[[str], None],
) -> Any:
    """Validate, create the gated task FIRST, publish with undo, take the slot LAST, open the gate.

    Returns the plan's ``result`` once the gate is open, or ``None`` with
    ``on_refused(why)`` called and nothing left changed. ``build`` may raise
    (a refusal before anything was spent); the caller treats that as one. A
    step's undo is registered before the step runs, so a step that changed
    something and then raised is undone too (Codex r1 1b). Once the gate is
    open nothing can raise into the caller: the run is the job's.
    """

    loop = asyncio.get_running_loop()
    for check in checks:
        why = check()
        if why is not None:
            on_refused(why)
            return None
    box = Box(inner)
    plan = build(box)
    entry = _entry(box, plan.start)
    try:
        box.task = loop.create_task(entry, context=contextvars.copy_context())
    except Exception as exc:  # noqa: BLE001 — nothing was spent
        entry.close()
        on_refused(f"the job's task could not be created: {type(exc).__name__}")
        return None
    done: list[Callable[[], None]] = []
    try:
        box.task.add_done_callback(functools.partial(_rescue, box=box, rescue=plan.rescue))
        for step, undo in plan.publish:
            done.append(undo)
            step()
        plan.take_slot()
    except Exception as exc:  # noqa: BLE001 — undone; the task returns at its first step
        for undo in reversed(done):
            with contextlib.suppress(Exception):
                undo()
        on_refused(f"publishing it failed: {type(exc).__name__}")
        return None
    box.published = True
    with contextlib.suppress(Exception):
        plan.announce()
    return plan.result


def task_factory_installed() -> str | None:
    """A refusal when the loop has a task factory: an eager one would start the job's task now (M4)."""

    if asyncio.get_running_loop().get_task_factory() is not None:
        return "a task factory is installed"
    return None


__all__ = [
    "MIGRATE",
    "AdoptedLink",
    "Allowlist",
    "Box",
    "CallDetach",
    "Detached",
    "Plan",
    "audit",
    "detach_text",
    "drive",
    "migrate_point",
    "product_allowlist",
    "run_ordered_commit",
]
