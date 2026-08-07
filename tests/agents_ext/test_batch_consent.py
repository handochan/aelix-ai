"""``request_spawn_consent_batch`` — ONE dialog for a WHOLE batch (P3 §3.7, S4).

The P2 gate asked once per spawn. A ``mode="parallel"`` / ``mode="chain"`` call
starts up to eight children from ONE tool call, and decision S4 says that is one
dialog rendering every member's task and the single cwd they share — not one
dialog per child (two modals from one call collide on ``tui/chrome.py:518``'s
single ``_modal`` slot) and emphatically not a remembered grant (the session memo
this module removed covered LATER calls, whose tasks nobody had seen).

WHAT MAKES THIS FILE DIFFERENT FROM ``test_spawn_consent.py``: a batch dialog can
be TOO TALL, and the failure is silent. ``ctx.ui.select`` composes title and
options into ONE non-wrapping, non-scrolling ``Window``
(``tui/context.py:112-129``, ``:419-422``) and ``tui/overlay.py``'s
``_CappedContainer`` clamps its height (``overlay.py:221-231``), so the overflow
is BOTTOM TRUNCATION — and ``build_options`` appends ``Cancel`` LAST. A naive
8-task dialog at 80x24 composes to 23 rows against a cap of 19: the hint, the
closing divider, the counter and ``Cancel`` are simply not drawn, and a
``down, Enter`` from a row that was never on screen grants ``auto-accept-edits``
to eight children. It bites from N >= 5.

So the assertions here are about the COMPOSED HEIGHT and about CONTENT:

* the composed height (``title_rows + option_rows + 4``) of every batch the fit
  check admits is asserted against the SHIPPED cap at 80x24 — ``24 - 5 = 19``,
  computed from ``tui/overlay.py``'s own ``_MODAL_RESERVE_ROWS`` floor rather
  than from the estimate ``consent.py`` uses, so the two are checked against each
  other rather than against themselves;
* the belt is that for every ``k`` the rendered title CONTAINS text derived from
  ``tasks[k]`` and that the preview-row count equals ``len(tasks)``. An index
  assertion would only prove that a number is on screen, and a number is not a
  task.

The refusal is a LIVE path, not a belt: a batch that will not fit returns a
NON-CONSENTED grant carrying :data:`~aelix_agents.consent.BATCH_TOO_TALL_REASON`,
no dialog is opened, and the caller blocks the call. Nothing is narrowed, and
nothing runs.

The fakes are deliberate copies of ``test_spawn_consent.py``'s rather than a
shared fixture: each states the property it exists to express (``has_ui`` is
TIME-VARYING, an answer may be anything at all), and the two files are owned by
different work packages.
"""

from __future__ import annotations

import itertools
import re

import aelix_agents.consent as consent_module
import pytest
from aelix_agents.consent import (
    BATCH_HEADER_ROWS,
    BATCH_TASK_PREVIEW_CHARS,
    CANCEL_OPTION,
    DIALOG_FIELD_CHARS,
    DIALOG_ROW_CHARS,
    MIN_TERMINAL_ROWS,
    batch_dialog_fits,
    build_batch_consent_title,
    build_consent_title,
    build_options,
    contains_control_chars,
    request_spawn_consent_batch,
)
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import ResolvedProfile

# The SHIPPED reserve, read off ``tui/overlay.py:57`` (``_MODAL_RESERVE_ROWS``)
# and NOT imported from ``consent.py``. ``consent._RESERVE_ESTIMATE`` is this
# plus one row of slack for the multi-row footer ``_reserve_rows`` can grow to
# (``overlay.py:160-195``); spelling the shipped floor here independently is what
# makes "the height the code admits fits the height the TUI will draw" a check
# rather than a tautology.
_SHIPPED_RESERVE_ROWS = 5
_MODAL_MIN_HEIGHT = 3  # ``overlay.py:61``

# Rows ``ctx.ui.select`` adds around a picker body: one divider under the title,
# one counter row, one closing divider, one hint row (``tui/context.py:112-129``,
# ``:365``).
_FRAME_ROWS = 4

_TALL = 60
"""A terminal tall enough that the fit check never fires — the tests that are
about the dialog's CONTENT must not be silently turned into refusal tests by the
height of whatever terminal the suite happens to run in."""

_PREVIEW_ROW = re.compile(r"^\[\d+/\d+\] ")


def _cap(rows: int, *, reserve: int = _SHIPPED_RESERVE_ROWS) -> int:
    """``tui/overlay.py``'s ``_modal_cap``, restated from the shipped source.

    ``max(_MODAL_MIN_HEIGHT, rows - _reserve_rows(chrome))`` (``:142-152``).
    """

    return max(_MODAL_MIN_HEIGHT, rows - reserve)


def _composed_rows(title: str, options: list[str]) -> int:
    """The height ``select`` will ask the terminal for, given this dialog."""

    return len(title.splitlines()) + len(options) + _FRAME_ROWS


def _preview_rows(title: str) -> list[str]:
    return [line for line in title.splitlines() if _PREVIEW_ROW.match(line)]


class _SelectSpy:
    """Async stand-in for ``ExtensionUIContext.select``.

    Records every ``(title, options)`` pair and returns whatever ``answers``
    yields — including ``None``, which is what Esc produces
    (``tui/context.py:255-262``).
    """

    def __init__(self, *answers: object) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, list[str]]] = []

    async def select(
        self, title: str, options: list[str], opts: object = None
    ) -> object:
        self.calls.append((title, list(options)))
        if not self._answers:
            return None
        return self._answers.pop(0)


class _FakeCtx:
    """Minimal :class:`ExtensionContext` surface: ``has_ui`` + ``ui``.

    ``has_ui`` is a property over a mutable flag because the real one
    (``extensions/api.py:1062`` → ``:1082-1083``) is TIME-VARYING, not a mode
    (finding OC-7).
    """

    def __init__(self, *, has_ui: bool, ui: _SelectSpy | None = None) -> None:
        self.flag = has_ui
        self.ui = ui if ui is not None else _SelectSpy()

    @property
    def has_ui(self) -> bool:
        return self.flag


def _resolved(
    *,
    scope: str = "user",
    approval_mode: str = "inherit",
    name: str = "scout",
    source_path: str = "/home/alice/.aelix/agents/scout.md",
) -> ResolvedProfile:
    profile = AgentProfile(
        name=name,
        description="A scouting agent.",
        body="You are a scout.",
        file_path=source_path,
        scope=scope,  # type: ignore[arg-type]
        approval_mode=approval_mode,  # type: ignore[arg-type]
    )
    return ResolvedProfile(
        name=name, profile=profile, source_path=source_path, scope=scope
    )


def _declaring(**kwargs: str) -> ResolvedProfile:
    """A profile that DECLARES it needs write authority (``approval_mode: auto``).

    Since the 2026-07-27 amendment this is the only kind of profile whose
    read-only spawn opens a dialog at all, so every test below that is about the
    dialog needs one.
    """

    kwargs.setdefault("approval_mode", "auto")
    return _resolved(**kwargs)  # type: ignore[arg-type]


_TASKS_8 = (
    "review the auth module for injection bugs",
    "summarise the release notes since v0.1.0",
    "list every TODO in the streaming package",
    "check the SBOM against the lockfile",
    "find dead code under scripts/",
    "count the skipped tests and say why",
    "read the ADR index and name the open residuals",
    "diff the two README translations",
)


@pytest.fixture(autouse=True)
def _tall_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts on a terminal where the fit check does NOT fire.

    The refusal tests set their own height explicitly. Without this the suite's
    verdict would depend on the terminal the developer happens to be sitting at —
    and CI, where ``get_terminal_size`` falls back to 24 lines, would disagree
    with a laptop.
    """

    monkeypatch.setattr(consent_module, "_terminal_rows", lambda: _TALL)


# --- one dialog, whole batch on screen --------------------------------------


async def test_an_eight_task_batch_opens_exactly_one_dialog() -> None:
    """S4's headline: one tool call, one decision, one modal.

    Not eight — ``mount_modal`` overwrites ``tui/chrome.py:518``'s single
    ``_modal`` slot unconditionally, so a second dialog from the same call
    orphans the first Future and hangs the turn.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8,
        PermissionMode.AUTO_ACCEPT,
        cwd="/w",
        mode="parallel",
    )

    assert len(spy.calls) == 1


async def test_every_member_task_is_on_screen_and_the_row_count_matches() -> None:
    """THE BELT, AND IT IS A CONTENT ASSERTION (P3 §3.7).

    For every ``k`` the rendered title must contain text derived from
    ``tasks[k]``, and the number of preview rows must equal ``len(tasks)``. An
    index assertion — "``[7/8]`` appears" — would pass on a dialog that rendered
    eight row numbers against seven tasks, or against the wrong tasks entirely.
    The number is not the task.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8,
        PermissionMode.AUTO_ACCEPT,
        cwd="/w",
        mode="parallel",
    )

    title, _options = spy.calls[0]
    rows = _preview_rows(title)
    assert len(rows) == len(_TASKS_8)
    for index, task in enumerate(_TASKS_8, start=1):
        # Every fixture task is well under the per-row budget, so the whole
        # string must be present — nothing here is dropped or clipped.
        assert task in title, task
        assert any(row.startswith(f"[{index}/8] ") and task in row for row in rows)


async def test_the_cwd_and_the_source_path_are_on_screen() -> None:
    """The two lines the model cannot influence, and the one it chose.

    ``cwd`` is on screen because the batch is the case where the model picks a
    directory once for eight children (finding OC-1's "in directories the MODEL
    chose"); ``source_path`` because it is the only line a human can go and read
    (ADR-0197 residual R2 mitigation 1).
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx,
        _declaring(source_path="/home/alice/.aelix/agents/auditor.md"),
        _TASKS_8,
        PermissionMode.AUTO_ACCEPT,
        cwd="/srv/checkout",
        mode="parallel",
    )

    title = spy.calls[0][0]
    assert "/srv/checkout" in title
    assert "/home/alice/.aelix/agents/auditor.md" in title


async def test_cancel_is_present_and_last() -> None:
    """The option that must never be the one that falls off the bottom."""

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx, _declaring(), _TASKS_8, PermissionMode.AUTO_ACCEPT, cwd="/w", mode="parallel"
    )

    options = spy.calls[0][1]
    assert options[-1] == CANCEL_OPTION


async def test_a_long_task_costs_exactly_one_row() -> None:
    """One member = one row, whatever the model wrote.

    Both halves matter. ``_truncate_task`` collapses whitespace, so a task made
    of newlines cannot become a 300-row modal; and the batch budget is
    :data:`BATCH_TASK_PREVIEW_CHARS`, so a long task is TRUNCATED VISIBLY rather
    than clipped invisibly at the terminal's right edge.
    """

    long_task = "audit " + "the streaming package and every adapter it loads " * 20
    multiline = "first line\n\n\nsecond line"
    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx,
        _declaring(),
        (long_task, multiline),
        PermissionMode.AUTO_ACCEPT,
        cwd="/w",
        mode="parallel",
    )

    title = spy.calls[0][0]
    rows = _preview_rows(title)
    assert len(rows) == 2
    # Derived independently of the implementation: the first 40 characters of
    # the model's text are on screen, and the row is bounded.
    assert long_task[:40] in rows[0]
    assert len(rows[0]) <= BATCH_TASK_PREVIEW_CHARS + len("[1/2] ")
    assert "first line second line" in rows[1]


# --- the composed height, which is the quantity that actually clips ----------


@pytest.mark.parametrize("mode", ["parallel", "chain"])
@pytest.mark.parametrize("n_options", [2, 3])
def test_every_admitted_batch_fits_the_shipped_cap_at_80x24(
    mode: str, n_options: int
) -> None:
    """``title_rows + option_rows + 4 <= cap(24)`` for every N the check admits.

    ``cap`` here is computed from ``tui/overlay.py``'s OWN reserve floor
    (``_MODAL_RESERVE_ROWS = 5``), not from ``consent.py``'s estimate, so this
    asserts the two agree in the safe direction rather than restating one of
    them. It also pins the tightness: the first N the check refuses must be one
    that genuinely does not fit under ``consent.py``'s own (deliberately
    stricter) budget — a check that refused everything would pass the first half
    of this test and be useless.
    """

    resolved = _declaring()
    admitted = 0
    for n_tasks in range(1, 9):
        tasks = tuple(f"task number {i}" for i in range(n_tasks))
        title = build_batch_consent_title(
            resolved, tasks, PermissionMode.PLAN, cwd="/w", mode=mode  # type: ignore[arg-type]
        )
        options = ["baseline", "widen", CANCEL_OPTION][:n_options]
        composed = _composed_rows(title, options)
        if batch_dialog_fits(n_tasks, n_options, mode=mode, rows=MIN_TERMINAL_ROWS):
            admitted = n_tasks
            assert composed <= _cap(MIN_TERMINAL_ROWS), (n_tasks, composed)
        else:
            # The first refusal, and everything above it. Under the estimate the
            # module actually budgets with, this composition does NOT fit.
            assert composed > _cap(
                MIN_TERMINAL_ROWS, reserve=consent_module._RESERVE_ESTIMATE
            ), (n_tasks, composed)
    assert admitted >= 2, "a 24-row terminal must still admit a real batch"


def test_the_naive_eight_task_dialog_is_the_one_that_would_have_clipped() -> None:
    """The measured regression, restated as a test (P3 §3.7, risk R9).

    Eight members plus three options on an 80x24 terminal composes past the cap —
    which is exactly why the fit check exists and why it must REFUSE rather than
    render. If a future edit makes the composition short enough to fit, this test
    fails and the author gets to delete it deliberately.
    """

    tasks = tuple(f"task number {i}" for i in range(8))
    title = build_batch_consent_title(
        _declaring(), tasks, PermissionMode.PLAN, cwd="/w", mode="parallel"
    )
    options = build_options(PermissionMode.PLAN, may_widen=True)

    assert _composed_rows(title, options) > _cap(MIN_TERMINAL_ROWS)
    assert not batch_dialog_fits(8, len(options), mode="parallel", rows=MIN_TERMINAL_ROWS)


def test_the_header_constant_matches_what_the_renderer_emits() -> None:
    """:data:`BATCH_HEADER_ROWS` is the measured budget's only link to the body.

    A row added to the title without touching the constant makes the fit check
    optimistic by exactly that row — and an optimistic fit check is a clipped
    ``Cancel``. Pinned for both modes, because ``chain`` spends one more.
    """

    for mode, extra in (("parallel", 0), ("chain", 1)):
        for n_tasks in (2, 5, 8):
            tasks = tuple(f"task {i}" for i in range(n_tasks))
            title = build_batch_consent_title(
                _declaring(), tasks, PermissionMode.PLAN, cwd="/w", mode=mode  # type: ignore[arg-type]
            )
            assert len(title.splitlines()) == BATCH_HEADER_ROWS + extra + n_tasks


def test_max_batch_members_agrees_with_the_fit_check_everywhere() -> None:
    """The number in the refusal message must be one the retry will accept.

    Told "split into calls of at most K", a model sends K — and being refused
    again is worse than being given no number. Checked over the whole grid rather
    than trusting the algebra.
    """

    for rows in range(10, 61):
        for mode in ("parallel", "chain"):
            for n_options in (2, 3):
                admits = consent_module._max_batch_members(
                    n_options, mode=mode, rows=rows
                )
                for n_tasks in range(1, 12):
                    assert batch_dialog_fits(
                        n_tasks, n_options, mode=mode, rows=rows
                    ) is (n_tasks <= admits), (rows, mode, n_options, n_tasks)


def test_a_short_terminal_admits_nothing_rather_than_something() -> None:
    """``_MIN_CAP`` is a floor on the CAP, never a licence to render.

    On a terminal so short that the cap floors out, the answer is refusal for
    every N — the floor must not become a budget.
    """

    assert not batch_dialog_fits(2, 2, mode="parallel", rows=4)
    assert consent_module._max_batch_members(2, mode="parallel", rows=4) == 0


# --- the refusal, which is a live path --------------------------------------


async def test_a_batch_that_will_not_fit_is_refused_with_no_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4: anything not on screen may not run. NOTHING is narrowed.

    The grant comes back non-consented — so the caller blocks the whole call —
    and no modal was opened at all: there is nothing to show, and opening one
    would also hold the process-wide consent lock for a decision nobody can make.
    """

    monkeypatch.setattr(consent_module, "_terminal_rows", lambda: MIN_TERMINAL_ROWS)
    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8,
        PermissionMode.AUTO_ACCEPT,
        cwd="/w",
        mode="parallel",
    )

    assert spy.calls == []
    assert grant.consented is False
    assert grant.widened is False
    assert "would not fit" in grant.reason
    assert "8 tasks" in grant.reason
    # The advice names a number the check will actually accept.
    assert re.search(r"at most (\d+) tasks", grant.reason)


async def test_the_refusal_reason_is_distinct_from_a_human_decline() -> None:
    """A refusal nobody was asked about must not be reported as "the user said no".

    The caller renders ``_DECLINED`` — "the user declined … do not retry it" — for
    an empty reason. Telling a model that a human refused a batch the human never
    saw is a lie it cannot act on; telling it the dialog was too tall is one it
    can (split the call).
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    declined = await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8[:3],
        PermissionMode.AUTO_ACCEPT,
        cwd="/w",
        mode="parallel",
    )

    assert declined.consented is False
    assert declined.reason == ""
    assert len(spy.calls) == 1


async def test_a_short_terminal_refuses_the_batch_the_tall_one_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same call, two terminals — and enlarging the terminal must work.

    This is the smoke item the plan makes mandatory, in unit form: an eight-task
    batch is refused at 24 rows and rendered in full at 60. A fit check hard-caped
    at ``MIN_TERMINAL_ROWS`` would refuse it on every terminal forever, which is
    not what §3.7 asks for.
    """

    spy = _SelectSpy(CANCEL_OPTION, CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    args = (ctx, _declaring(), _TASKS_8, PermissionMode.AUTO_ACCEPT)

    monkeypatch.setattr(consent_module, "_terminal_rows", lambda: MIN_TERMINAL_ROWS)
    refused = await request_spawn_consent_batch(*args, cwd="/w", mode="parallel")
    assert refused.consented is False
    assert spy.calls == []

    monkeypatch.setattr(consent_module, "_terminal_rows", lambda: _TALL)
    await request_spawn_consent_batch(*args, cwd="/w", mode="parallel")
    assert len(spy.calls) == 1
    assert len(_preview_rows(spy.calls[0][0])) == 8


def _pin_ioctl(monkeypatch: pytest.MonkeyPatch, rows: int | None) -> None:
    """Pin what the OS reports for the real output.

    ``None`` = "not a terminal", which is the headless case AND the case this
    suite normally runs in (pytest replaces fd 1 with a temp file, so the ioctl
    raises ``OSError``). Pinned rather than inherited so this file's verdict does
    not depend on whether the developer passed ``-s`` from a 24-row window.
    """

    monkeypatch.setattr(consent_module, "_ioctl_rows", lambda: rows)


def test_terminal_rows_reads_the_live_terminal_and_floors_a_bad_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$LINES`` is what ``shutil.get_terminal_size`` answers from first.

    The floor matters more than the reading: an unmeasurable terminal is treated
    as a SMALL one, because admitting one member too few costs a split call while
    admitting one too many costs an unseen ``Cancel``.
    """

    monkeypatch.undo()  # drop the autouse override; this test is about the real one
    _pin_ioctl(monkeypatch, None)  # no tty to cross-check against
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "47")
    assert consent_module._terminal_rows() == 47

    monkeypatch.setattr(
        consent_module.shutil,
        "get_terminal_size",
        lambda fallback=(80, 24): type("_S", (), {"lines": 0, "columns": 80})(),
    )
    assert consent_module._terminal_rows() == MIN_TERMINAL_ROWS


def test_a_lying_LINES_can_never_make_the_fit_check_more_permissive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding F4. ``$LINES`` may shrink this measurement, never grow it.

    ``shutil.get_terminal_size`` consults ``$LINES`` BEFORE it asks the OS. The
    renderer this function exists to predict does not — prompt_toolkit asks its
    output — so a stale or exported ``LINES`` (tmux, a CI wrapper, a shell that
    exported it before a resize) made the budget measure a terminal that is not
    the one being drawn to. Measured before the fix: ``LINES=200`` on a 24-row
    screen admitted an eight-task chain, i.e. ``Cancel`` off the bottom, which is
    the exact failure §3.7 exists to prevent.

    Both directions are asserted, because a fix that simply ignored ``$LINES``
    would pass the first half and break the deliberate ``LINES`` handling the
    headless and piped cases rely on.
    """

    monkeypatch.undo()
    monkeypatch.setenv("COLUMNS", "80")

    # PERMISSIVE direction — the ioctl wins.
    monkeypatch.setenv("LINES", "200")
    _pin_ioctl(monkeypatch, 24)
    assert consent_module._terminal_rows() == 24
    assert not batch_dialog_fits(
        8, 3, mode="chain", rows=consent_module._terminal_rows()
    )

    # RESTRICTIVE direction — the smaller reading still wins, whichever side it
    # came from, because this file may only ever err toward a SHORT terminal.
    monkeypatch.setenv("LINES", "10")
    _pin_ioctl(monkeypatch, 60)
    assert consent_module._terminal_rows() == 10

    # NO TTY — nothing to cross-check, so the environment stays authoritative.
    monkeypatch.setenv("LINES", "60")
    _pin_ioctl(monkeypatch, None)
    assert consent_module._terminal_rows() == 60


def test_ioctl_rows_is_none_when_the_output_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``None`` branch is a live path, not a defensive one.

    Under ``-p`` / ``--mode json``, under a pipe, and under this very suite, fd 1
    is not a tty and ``os.get_terminal_size`` raises ``OSError``. If that raised
    out of :func:`_terminal_rows` the batch door would crash instead of prompting.
    """

    monkeypatch.undo()

    def _boom(_fd: int) -> object:
        raise OSError(25, "Inappropriate ioctl for device")

    monkeypatch.setattr(consent_module.os, "get_terminal_size", _boom)
    assert consent_module._ioctl_rows() is None


# --- the answers ------------------------------------------------------------


async def test_a_declined_batch_starts_nothing() -> None:
    """One ``Cancel`` refuses the WHOLE call — there is no per-member answer."""

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8,
        PermissionMode.AUTO_ACCEPT,
        cwd="/w",
        mode="parallel",
    )

    assert grant.consented is False
    assert grant.widened is False


async def test_esc_is_a_decline() -> None:
    """``None`` is what ``tui/context.py:255-262`` returns for Esc."""

    spy = _SelectSpy(None)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _declaring(), _TASKS_8, PermissionMode.AUTO_ACCEPT, cwd="/w", mode="parallel"
    )

    assert grant.consented is False


async def test_an_answer_nobody_was_shown_is_a_decline() -> None:
    """The allow-list, on the door where one answer buys eight children.

    ``ctx.ui`` is a public Protocol seam any host may supply
    (``extensions/ext_ui.py:186-193``), and the P2 measurement was that a
    deny-list form turned an unrecognised string into CONSENT.
    """

    spy = _SelectSpy("<<< user pressed something weird >>>")
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _declaring(), _TASKS_8, PermissionMode.YOLO, cwd="/w", mode="parallel"
    )

    assert grant.consented is False
    assert grant.widened is False


async def test_the_widening_answer_widens_the_whole_batch_to_auto_accept() -> None:
    """One decision, one mode — every member runs under it (S3).

    And never above ``auto-accept-edits``: the ceiling is the same one the
    single-task dialog enforces, so a batch cannot buy authority a single spawn
    could not.
    """

    options = build_options(PermissionMode.PLAN, may_widen=True)
    spy = _SelectSpy(options[1])
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8[:4],
        PermissionMode.DEFAULT,
        cwd="/w",
        mode="parallel",
    )

    assert grant.consented is True
    assert grant.widened is True
    assert grant.mode is PermissionMode.AUTO_ACCEPT


async def test_the_grant_names_the_one_profile_the_batch_runs_as() -> None:
    """S3 — one profile per call, so the grant stays singular and checkable.

    ``extension.py``'s identity re-check compares the resolved profile against
    THESE fields at execute time; if a batch produced anything plural there would
    be nothing single to compare against.
    """

    spy = _SelectSpy(build_options(PermissionMode.PLAN, may_widen=True)[0])
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx,
        _declaring(name="auditor", source_path="/u/.aelix/agents/auditor.md"),
        _TASKS_8[:3],
        PermissionMode.DEFAULT,
        cwd="/w",
        mode="parallel",
    )

    assert grant.profile == "auditor"
    assert grant.source_path == "/u/.aelix/agents/auditor.md"
    assert grant.scope == "user"
    assert grant.consented is True


async def test_two_batches_ask_twice() -> None:
    """NO MEMO, and a batch is not the rung above one.

    The removed session memo let a LATER call skip the dialog. A batch is one
    call; the next call gets its own dialog, whatever it delegates.
    """

    baseline = build_options(PermissionMode.PLAN, may_widen=True)[0]
    spy = _SelectSpy(baseline, baseline)
    ctx = _FakeCtx(has_ui=True, ui=spy)
    resolved = _declaring()

    for tasks in (_TASKS_8[:2], _TASKS_8[2:4]):
        grant = await request_spawn_consent_batch(
            ctx, resolved, tasks, PermissionMode.DEFAULT, cwd="/w", mode="parallel"
        )
        assert grant.consented is True

    assert len(spy.calls) == 2


# --- the common case: ZERO dialogs ------------------------------------------


async def test_a_default_parent_fans_out_with_no_dialog_at_all() -> None:
    """THE COMMON CASE, AND IT MUST STAY FREE (S4, ``consent.py:458-472``).

    A ``default`` parent clamps a non-declaring profile to ``plan``:
    ``grants_write_authority`` is False and nothing may be widened, so eight
    read-only children start with no modal — and therefore with no height budget
    and no possibility of a "will not fit" refusal.
    """

    spy = _SelectSpy()
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _resolved(), _TASKS_8, PermissionMode.DEFAULT, cwd="/w", mode="parallel"
    )

    assert spy.calls == []
    assert grant.consented is True
    assert grant.widened is False
    assert grant.mode is PermissionMode.PLAN
    assert grant.reason == ""


async def test_the_common_case_is_not_refused_on_a_short_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The height budget must never reach a call that renders no dialog.

    Otherwise a 24-row terminal would silently cap ordinary read-only fan-out at
    six members — a governance rule leaking into a case it has no business in.
    """

    monkeypatch.setattr(consent_module, "_terminal_rows", lambda: 10)
    spy = _SelectSpy()
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _resolved(), _TASKS_8, PermissionMode.DEFAULT, cwd="/w", mode="parallel"
    )

    assert spy.calls == []
    assert grant.consented is True


async def test_headless_takes_the_clamp_and_never_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-p`` / ``--mode json`` / RPC: a silent DOWNGRADE, never a new refusal.

    There is no modal to truncate, so the height budget does not apply even at
    eight members on a ten-row terminal.
    """

    monkeypatch.setattr(consent_module, "_terminal_rows", lambda: 10)
    spy = _SelectSpy()
    ctx = _FakeCtx(has_ui=False, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _declaring(), _TASKS_8, PermissionMode.YOLO, cwd="/w", mode="parallel"
    )

    assert spy.calls == []
    assert grant.consented is True
    assert grant.widened is False
    assert grant.mode is PermissionMode.AUTO_ACCEPT


# --- chain: the text that is NOT on screen (§3.1.1) -------------------------


async def test_a_chain_dialog_says_later_steps_carry_unshown_text() -> None:
    """§3.1.1 mitigation 2 — the one row that makes the human's decision honest.

    What reaches step ``k >= 2`` is ``render_step(task_k, summary_{k-1})``: text
    minted mid-call by a child that has itself read ``cwd`` content an attacker
    may control. No dialog can show it, because it does not exist yet. Saying so
    costs one row and is charged against the height budget like any other.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx, _declaring(), _TASKS_8[:3], PermissionMode.DEFAULT, cwd="/w", mode="chain"
    )

    title = spy.calls[0][0]
    assert "written by an earlier agent" in title
    assert "not shown here" in title


async def test_a_parallel_dialog_carries_no_chain_warning() -> None:
    """Parallel members share no text, so the warning would be false.

    A warning that appears on dialogs it does not apply to is how a real one
    stops being read.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8[:3],
        PermissionMode.DEFAULT,
        cwd="/w",
        mode="parallel",
    )

    title = spy.calls[0][0]
    assert "written by an earlier agent" not in title


async def test_a_widenable_parallel_batch_offers_the_same_three_options() -> None:
    """Parallel is P2's dialog with more rows — the rungs are unchanged."""

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8[:3],
        PermissionMode.DEFAULT,
        cwd="/w",
        mode="parallel",
    )

    assert spy.calls[0][1] == build_options(PermissionMode.PLAN, may_widen=True)
    assert len(spy.calls[0][1]) == 3


async def test_a_widenable_chain_batch_offers_only_two() -> None:
    """§3.1.1 mitigation 3 — a chain dialog may take the clamp, never widen it.

    The same profile, the same parent, the same clamp: only ``mode`` differs, and
    the widening rung is gone. A human may CONFIRM authority that exists; this
    dialog may not MANUFACTURE it for prompts a child process will write later.
    The dialog itself still fires — ``Cancel`` is a real answer for a chain of
    model-written steps — which is why ``consent_is_required`` takes no ``mode``.
    """

    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    await request_spawn_consent_batch(
        ctx, _declaring(), _TASKS_8[:3], PermissionMode.DEFAULT, cwd="/w", mode="chain"
    )

    options = spy.calls[0][1]
    assert options == build_options(PermissionMode.PLAN, may_widen=False)
    assert len(options) == 2
    assert options[-1] == CANCEL_OPTION
    assert not any("Allow file edits" in option for option in options)


async def test_a_chain_cannot_be_widened_by_an_answer_nobody_was_offered() -> None:
    """The rung is not merely hidden — the answer does not work either.

    A ``ctx.ui`` implementation that returned the widening label anyway (a stale
    render, a Web UI replaying a previous dialog) must not widen a chain: the
    string is not in the options that were rendered, so the allow-list declines
    it outright.
    """

    widening_label = build_options(PermissionMode.PLAN, may_widen=True)[1]
    spy = _SelectSpy(widening_label)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _declaring(), _TASKS_8[:3], PermissionMode.DEFAULT, cwd="/w", mode="chain"
    )

    assert grant.widened is False
    assert grant.consented is False
    assert grant.mode is PermissionMode.PLAN


async def test_a_chain_under_a_loose_parent_still_runs_write_capable() -> None:
    """Mitigation 3 removes a RUNG, not the parent's own authority.

    A write-capable clamp came from the human's posture, not from this dialog, so
    refusing to widen must not tighten it either — that would make ``chain``
    quietly weaker than ``single`` for the same profile.
    """

    baseline = build_options(PermissionMode.AUTO_ACCEPT, may_widen=False)[0]
    spy = _SelectSpy(baseline)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx,
        _resolved(approval_mode="inherit"),
        _TASKS_8[:3],
        PermissionMode.AUTO_ACCEPT,
        cwd="/w",
        mode="chain",
    )

    assert grant.consented is True
    assert grant.widened is False
    assert grant.mode is PermissionMode.AUTO_ACCEPT


def test_an_unknown_mode_cannot_widen() -> None:
    """ALLOW-LIST, not ``mode != "chain"``.

    A topology added after this file must inherit the answer that grants nothing,
    the same way an unrecognised ``approval_mode`` declares nothing
    (``posture._DECLARES_WRITE_AUTHORITY``).
    """

    resolved = _declaring()
    assert consent_module._may_widen(
        resolved, PermissionMode.PLAN, has_ui=True, mode="parallel"
    )
    for mode in ("chain", "swarm", "", "SINGLE"):
        assert not consent_module._may_widen(
            resolved, PermissionMode.PLAN, has_ui=True, mode=mode
        ), mode


# --- a str is a Sequence[str] -----------------------------------------------


async def test_a_bare_string_is_a_typeerror_not_23_dialog_rows() -> None:
    """THE SILENT-CATASTROPHE PIN (P3 WP-4).

    An earlier draft re-typed the single-task ``task`` parameter to
    ``Sequence[str]``. ``str`` SATISFIES that annotation, so
    ``/agents run scout "review the auth module"`` (``runtime.py:330-336`` passes
    a bare ``str``) would have type-checked green and rendered *"Delegate 23
    tasks…"* with one row per CHARACTER — on the one door a human typed. The
    signatures were kept single-task for that reason, and the batch door guards
    the same mistake explicitly, because an annotation a ``str`` satisfies is not
    a guard.
    """

    ctx = _FakeCtx(has_ui=True)
    with pytest.raises(TypeError, match="not a str"):
        await request_spawn_consent_batch(
            ctx,
            _declaring(),
            "review the auth module",  # type: ignore[arg-type]
            PermissionMode.AUTO_ACCEPT,
            cwd="/w",
            mode="parallel",
        )
    assert ctx.ui.calls == []


def test_the_renderer_refuses_a_bare_string_too() -> None:
    """Both batch entry points, because either could be reached first."""

    with pytest.raises(TypeError, match="not a str"):
        build_batch_consent_title(
            _declaring(),
            "review the auth module",  # type: ignore[arg-type]
            PermissionMode.PLAN,
            cwd="/w",
            mode="parallel",
        )


async def test_an_empty_batch_is_a_programming_error() -> None:
    """``AgentCall.tasks`` is "ALWAYS at least one" (``tool.py:249``).

    An empty tuple would render *"Delegate 0 tasks"* and consent to nothing;
    raising is how a caller that lost the tasks finds out immediately.
    """

    ctx = _FakeCtx(has_ui=True)
    with pytest.raises(ValueError, match="empty"):
        await request_spawn_consent_batch(
            ctx, _declaring(), (), PermissionMode.AUTO_ACCEPT, cwd="/w", mode="parallel"
        )
    assert ctx.ui.calls == []


# --- one member is a P2 dialog ----------------------------------------------


async def test_a_one_member_batch_renders_the_single_task_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte-identical to P2, and it must stay that way.

    The batch renderer's plural heading and 72-character preview would be a
    gratuitous change for the shape that is already shipped. A one-step chain also
    keeps the widening rung: there is no step 2, so §3.1.1's unshown text does not
    exist for it.
    """

    task = "review the auth module for injection bugs"
    for mode in ("parallel", "chain"):
        spy = _SelectSpy(CANCEL_OPTION)
        ctx = _FakeCtx(has_ui=True, ui=spy)
        resolved = _declaring()

        await request_spawn_consent_batch(
            ctx, resolved, (task,), PermissionMode.DEFAULT, cwd="/w", mode=mode  # type: ignore[arg-type]
        )

        title, options = spy.calls[0]
        assert title == build_consent_title(
            resolved, task, PermissionMode.PLAN, cwd="/w"
        )
        assert options == build_options(PermissionMode.PLAN, may_widen=True)


async def test_a_one_member_batch_is_never_refused_for_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2's dialog fits every terminal it ever fitted.

    The height budget arrived with batches; applying it to the single-task shape
    would turn a working dialog into a refusal on a short terminal, which is a
    regression P3 has no licence to ship.
    """

    monkeypatch.setattr(consent_module, "_terminal_rows", lambda: 10)
    spy = _SelectSpy(CANCEL_OPTION)
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _declaring(), ("one task",), PermissionMode.AUTO_ACCEPT, cwd="/w", mode="parallel"
    )

    assert len(spy.calls) == 1
    assert grant.reason == ""


def test_the_module_still_exports_no_session_memo() -> None:
    """The batch is not the rung the removed memo was.

    ``consent.py:125-143`` forbids a memo; P3 adds a batch. If a future edit
    smuggles the memo back in beside the batch, this fails first.
    """

    for banned in ("SessionGrantMemo", "REMEMBER_OPTION", "_may_remember"):
        assert not hasattr(consent_module, banned), banned
    assert not any("REMEMBER" in name for name in consent_module.__all__)


def test_the_batch_signature_carries_no_memo_and_no_options_parameter() -> None:
    """The batch door takes a tuple of tasks, a cwd and a mode. Nothing else.

    In particular there is no "options" or "preview" parameter through which a
    caller could shorten what the human is shown, and no memo — the two shapes
    that would each re-open finding OC-1 from a different direction.

    ``model`` is the one addition and it is the opposite shape: it can only ADD a
    row, it is what the child will be launched with, and nothing it carries
    reaches the grant. It also makes the dialog TALLER, so it can only ever cause
    a batch to be refused — never one to be shown short.
    """

    import inspect

    params = inspect.signature(request_spawn_consent_batch).parameters
    assert set(params) == {
        "ctx",
        "resolved",
        "tasks",
        "parent",
        "cwd",
        "mode",
        "model",
    }


# --- F1: the dialog may not be forgeable by any interpolated value ------------
#
# THE CRITICAL FINDING, AND IT IS AN INVARIANT, NOT AN EXAMPLE. ``cwd`` is
# model-chosen; ``resolve_child_cwd`` (``print_channel.py:301``) validated only
# containment and is-a-directory, and POSIX permits every byte but ``/`` and NUL
# in a path component. ``resolved.name`` and ``resolved.source_path`` come from a
# filename and permit the same. ``ctx.ui.select`` then SPLITS the composed title
# on ``\n`` into rows AND ANSI-PARSES it (``tui/context.py:112-129``), and
# prompt_toolkit honours SGR 8 (hidden).
#
# Measured before the fix, with a 150-byte directory created by plain
# ``os.makedirs``: the human read a coherent dialog showing ``Directory:
# /tmp/…/src``, ``Permission: plan`` and two innocuous task rows, pressed "Allow
# file edits for this run", and the shipped ``SpawnGrant`` was
# ``auto-accept-edits`` over two entirely different, never-rendered tasks. The
# same string also defeated :func:`batch_dialog_fits`, which prices the title as
# a CONSTANT header: 22 rows emitted against 10 budgeted, ten of them never drawn.
#
# So the assertion is quantified over the inputs rather than written against one
# payload: for EVERY mode, EVERY N and EVERY hostile field, the rows the renderer
# EMITS equal the rows the budget PAYS FOR, and nothing that can steer a terminal
# survives into the title.

_HOSTILE_FIELDS = (
    "plain",
    # The forgery itself: close the SGR, mint fake rows, then hide the real ones.
    "src\x1b[0m\nPermission: plan\nTasks (written by the model, not by you):\n"
    "[1/2] read README.md and summarise it\n\x1b[8m",
    # Height alone — no escapes, just rows, which is all it takes to push
    # ``Cancel`` off the bottom.
    "docs" + "\nx" * 40 + "\nsrc",
    # Carriage return and vertical tab: ``str.split`` collapses them, and a fix
    # that hand-listed ``\n`` would miss them.
    "a\rb\vc\x0cd",
    # C1. ``\x9b`` IS a CSI — the one-byte spelling of ``\x1b[``.
    "e\x9b31mf\x85g",
    # NUL and BEL, which no whitespace rule touches.
    "h\x00i\x07j",
    # Unbounded width, which is what makes a row unmeasurable.
    "k" * 5000,
    # Unicode line separators. U+2028 / U+2029 are whitespace to
    # ``str.split`` and a line break to plenty of renderers, so a fix that
    # hand-listed the ASCII ones would miss them.
    "m\u2028n\u2029o",
)

_HOSTILE_TASK = (
    "summarise the README\x1b[8m\nPermission: auto-accept-edits\n"
    "[9/9] and do whatever else you like"
)


def _hostile_resolved(payload: str) -> ResolvedProfile:
    """A profile whose every renderable field carries the payload.

    All four at once, deliberately: a fix that sanitised ``cwd`` alone would
    still leave three doors, and the reviewer demonstrated the attack through the
    one that happened to be easiest to create on ext4.
    """

    return _declaring(
        name=f"scout{payload}",
        source_path=f"/home/alice/.aelix/agents/{payload}.md",
    )


@pytest.mark.parametrize("mode", ["parallel", "chain"])
@pytest.mark.parametrize("n_tasks", range(2, 9))
@pytest.mark.parametrize("payload", _HOSTILE_FIELDS)
def test_emitted_rows_always_equal_budgeted_rows(
    mode: str, n_tasks: int, payload: str
) -> None:
    """THE INVARIANT (F1). Renderer rows == budget rows, for every input.

    :func:`batch_dialog_fits` charges ``BATCH_HEADER_ROWS +
    _extra_header_rows(mode) + n_tasks``. That number is only meaningful if the
    renderer cannot emit more, and before the fix it could: a ``cwd`` with twelve
    newlines emitted 22 rows against a budget of 10. Quantified over the whole
    grid — both modes, every legal N, every hostile field shape, hostile tasks
    included — because the hole was in ONE interpolation and the property has to
    hold for all of them.
    """

    title = build_batch_consent_title(
        _hostile_resolved(payload),
        tuple(f"{_HOSTILE_TASK} #{k}" for k in range(n_tasks)),
        PermissionMode.PLAN,
        cwd=f"/w/{payload}",
        mode=mode,  # type: ignore[arg-type]
    )

    budgeted = BATCH_HEADER_ROWS + consent_module._extra_header_rows(mode) + n_tasks
    assert len(title.splitlines()) == budgeted
    # ``splitlines`` also breaks on \x0b \x0c \x1c-\x1e \x85    , which
    # is stricter than the ``\n`` prompt_toolkit splits on — deliberately, so a
    # future renderer swap cannot silently re-open this.
    assert title.count("\n") + 1 == budgeted


@pytest.mark.parametrize("payload", _HOSTILE_FIELDS)
def test_no_interpolated_field_can_carry_a_control_character(payload: str) -> None:
    """No ESC in the title means no SGR, no hidden rows and no cursor moves.

    Asserted on the composed string rather than per field, so a field added later
    is covered by construction. Both doors, because the single-task one is the P2
    door and had the identical hole.
    """

    resolved = _hostile_resolved(payload)
    batch = build_batch_consent_title(
        resolved,
        (_HOSTILE_TASK, _HOSTILE_TASK),
        PermissionMode.PLAN,
        cwd=f"/w/{payload}",
        mode="parallel",
    )
    single = build_consent_title(
        resolved, _HOSTILE_TASK, PermissionMode.PLAN, cwd=f"/w/{payload}"
    )

    for title in (batch, single):
        body = title.replace("\n", "")
        assert not contains_control_chars(body)
        assert "\x1b" not in body


@pytest.mark.parametrize("payload", _HOSTILE_FIELDS)
def test_every_header_row_stays_within_one_visible_row(payload: str) -> None:
    """Width, not just height. The modal does NOT wrap.

    ``tui/context.py:419-422`` builds its ``Window`` with ``wrap_lines`` left at
    ``False``, so an over-long row is clipped at the terminal edge WITH NO MARKER
    — the human sees a plausible prefix and cannot tell there is more.

    THE ROW IS THE UNIT, NOT THE FIELD, and that is the whole point of asserting
    it here: ``Profile:`` carries the name AND the scope plus fixed text, so
    per-field limits that each fit a row do not compose into a row that fits. An
    earlier draft of this test bounded the fields and passed while the composed
    ``Profile:`` row was 93 columns.
    """

    title = build_batch_consent_title(
        _hostile_resolved(payload),
        (_HOSTILE_TASK, _HOSTILE_TASK),
        PermissionMode.PLAN,
        cwd=f"/w/{payload}",
        mode="parallel",
    )

    for row in title.splitlines():
        # Every row EXCEPT the chain warning, which §3.1.1 accepts as 81 columns
        # with its last character clipped, and the task previews, which
        # ``BATCH_TASK_PREVIEW_CHARS`` bounds on its own budget.
        if row.startswith(("Delegate", "Profile:", "Source:", "Directory:", "Permission:")):
            assert len(row) <= DIALOG_ROW_CHARS, row


def test_a_truncated_directory_row_still_shows_the_LAST_component() -> None:
    """Elide the MIDDLE of a path, never the tail — and say so on screen.

    Containment only promises the child runs somewhere inside the parent tree;
    WHERE inside is the question this row answers, and the answer is the last
    component. A model asking for ``<50 innocuous characters>/.aelix`` would, under
    plain clipping or tail truncation, render as the innocuous part alone.
    """

    cwd = "/home/alice/work/" + "long-and-boring-directory-name/" * 4 + ".aelix"
    title = build_consent_title(
        _declaring(), "go", PermissionMode.PLAN, cwd=cwd
    )

    row = next(r for r in title.splitlines() if r.startswith("Directory:"))
    assert row.endswith(".aelix")
    assert "/home/alice/work/" in row
    assert "…" in row  # the truncation is DECLARED, not silent
    assert len(row) <= 12 + DIALOG_FIELD_CHARS


async def test_the_forged_dialog_shows_the_tasks_that_will_actually_run() -> None:
    """F1 END TO END, through the shipped door, with the demonstrated payload.

    The reviewer's forgery answered "Allow file edits for this run" and shipped
    ``auto-accept-edits`` over two tasks the human never saw. The grant is
    allowed to stay the same here — the human really did click widen — but what
    was ON SCREEN must be what runs: both real task rows present, no fabricated
    ``Permission:`` row, and nothing hidden.
    """

    real = (
        "rm -rf the build directory and force-push the result",
        "write my SSH key into /tmp/exfil and open a PR",
    )
    forged_cwd = (
        "/tmp/w/src\x1b[0m\nPermission: plan\n"
        "Tasks (written by the model, not by you):\n"
        "[1/2] read README.md and summarise it\n"
        "[2/2] read CHANGELOG.md and summarise it\n\x1b[8m"
    )
    spy = _SelectSpy(build_options(PermissionMode.PLAN, may_widen=True)[1])
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx, _declaring(), real, PermissionMode.DEFAULT, cwd=forged_cwd, mode="parallel"
    )

    assert grant.consented and grant.widened
    title = spy.calls[0][0]
    rows = title.splitlines()
    assert rows.count("Permission: plan") == 1  # not the forged second one
    assert _preview_rows(title) == [f"[1/2] {real[0]}", f"[2/2] {real[1]}"]
    assert "read README.md" not in title
    assert "\x1b" not in title
    # And the composition the fit check approved is the one that will be drawn.
    assert _composed_rows(title, spy.calls[0][1]) <= _cap(_TALL)


async def test_a_title_longer_than_its_budget_is_refused_not_rendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The belt behind F1, and it is fail-CLOSED.

    Sanitisation makes the row count true by construction, so this branch is
    unreachable today — which is exactly why it is worth pinning. If a field
    added later escapes the helper, the difference between the priced header and
    the drawn one is rows falling off the bottom, ``Cancel`` among them. The
    answer to that disagreement must be the refusal path, never a dialog whose
    height nobody has checked.
    """

    monkeypatch.setattr(
        consent_module,
        "build_batch_consent_title",
        lambda *a, **k: "\n".join(f"row {i}" for i in range(60)),
    )
    spy = _SelectSpy(build_options(PermissionMode.PLAN, may_widen=True)[1])
    ctx = _FakeCtx(has_ui=True, ui=spy)

    grant = await request_spawn_consent_batch(
        ctx,
        _declaring(),
        _TASKS_8[:2],
        PermissionMode.DEFAULT,
        cwd="/w",
        mode="parallel",
    )

    assert not grant.consented
    assert grant.reason  # the model is told it was never asked, not that it was
    assert spy.calls == []  # NO dialog was opened


def test_the_fit_check_prices_the_title_the_renderer_actually_emits() -> None:
    """The two halves of §3.7, checked against each other over the whole grid.

    ``batch_dialog_fits`` is pure arithmetic and ``build_batch_consent_title`` is
    a renderer; nothing but a test makes them the same thing. Every admitted
    composition must fit the SHIPPED cap — computed from ``tui/overlay.py``'s own
    reserve, independently of ``consent._RESERVE_ESTIMATE``.
    """

    for mode, n_tasks, payload, rows in itertools.product(
        ("parallel", "chain"), range(2, 9), _HOSTILE_FIELDS, (24, 40, 60)
    ):
        options = build_options(PermissionMode.PLAN, may_widen=True)
        if not batch_dialog_fits(n_tasks, len(options), mode=mode, rows=rows):
            continue
        title = build_batch_consent_title(
            _hostile_resolved(payload),
            tuple(_HOSTILE_TASK for _ in range(n_tasks)),
            PermissionMode.PLAN,
            cwd=f"/w/{payload}",
            mode=mode,  # type: ignore[arg-type]
        )
        assert _composed_rows(title, options) <= _cap(rows), (mode, n_tasks, rows)


# === the Model row, and what it costs the height budget =======================
#
# The batch dialog gained the same row the single-task one did. It is a HEADER
# row, so it is charged: :func:`_extra_header_rows` prices exactly what
# :func:`build_batch_consent_title` emits, by asking the same function.


def test_the_batch_dialog_names_the_model() -> None:
    """One row, below ``Permission:``, in the same label style as the rest."""

    title = build_batch_consent_title(
        _declaring(),
        _TASKS_8[:3],
        PermissionMode.PLAN,
        cwd="/w",
        mode="parallel",
        model="claude-opus-4-8",
    )
    rows = title.splitlines()

    assert "Model:      claude-opus-4-8" in rows
    assert rows.index("Model:      claude-opus-4-8") == rows.index(
        "Permission: plan"
    ) + 1


def test_the_header_constant_still_matches_what_the_renderer_emits_with_a_model() -> None:
    """The F1/§3.7 invariant, extended to the new row.

    ``BATCH_HEADER_ROWS + _extra_header_rows(mode, model) + n_tasks`` must equal
    the rows actually drawn — for BOTH modes, with and without a model, and for a
    model that sanitises to NOTHING (which must be un-emitted and unpaid-for by
    the same decision, or the budget and the body disagree by one row and the row
    that falls off the bottom is ``Cancel``).
    """

    for mode in ("parallel", "chain"):
        for model in (None, "claude-opus-4-8", "\x1b\x9b\n\t", 12345):
            for n_tasks in (2, 5, 8):
                tasks = tuple(f"task {i}" for i in range(n_tasks))
                title = build_batch_consent_title(
                    _declaring(),
                    tasks,
                    PermissionMode.PLAN,
                    cwd="/w",
                    mode=mode,  # type: ignore[arg-type]
                    model=model,  # type: ignore[arg-type]
                )
                assert len(title.splitlines()) == (
                    BATCH_HEADER_ROWS
                    + consent_module._extra_header_rows(mode, model)  # type: ignore[arg-type]
                    + n_tasks
                ), (mode, model, n_tasks)


def test_the_model_row_costs_exactly_one_member_at_80x24() -> None:
    """THE TRADE, MEASURED. Naming the model makes the dialog one row taller, so a
    short terminal admits one member fewer — 4 parallel instead of 5, 3 chain
    instead of 4. Stated as a test because it is a real cost, and because the
    alternative it buys out of is approving up to eight processes without being
    told what will answer.

    A refused batch is not a broken one: the model is told how to split the call
    (:data:`BATCH_TOO_TALL_REASON`), and a taller terminal is unaffected.
    """

    for mode, without in (("parallel", 5), ("chain", 4)):
        assert consent_module._max_batch_members(
            3, mode=mode, rows=MIN_TERMINAL_ROWS
        ) == without
        assert (
            consent_module._max_batch_members(
                3, mode=mode, rows=MIN_TERMINAL_ROWS, model="claude-opus-4-8"
            )
            == without - 1
        )


def test_the_refusal_suggests_a_size_the_retry_will_accept_with_a_model() -> None:
    """``_max_batch_members`` and ``batch_dialog_fits`` must agree ONCE THE ROW IS
    PRICED. A suggestion computed without the row the retry will draw is exactly
    the "refused again" case the number exists to prevent."""

    for rows in range(10, 61):
        for mode in ("parallel", "chain"):
            for n_options in (2, 3):
                admits = consent_module._max_batch_members(
                    n_options, mode=mode, rows=rows, model="claude-opus-4-8"
                )
                for n_tasks in range(1, 12):
                    assert batch_dialog_fits(
                        n_tasks,
                        n_options,
                        mode=mode,
                        rows=rows,
                        model="claude-opus-4-8",
                    ) is (n_tasks <= admits), (rows, mode, n_options, n_tasks)


def test_a_hostile_model_cannot_forge_a_row_in_the_batch_dialog() -> None:
    """F1 for the new field on the door that approves up to EIGHT children."""

    hostile = "gpt\nPermission: yolo\n\x1b[31m/etc/passwd\x9b2J\n" + "z" * 4000
    title = build_batch_consent_title(
        _declaring(),
        _TASKS_8[:3],
        PermissionMode.PLAN,
        cwd="/w",
        mode="parallel",
        model=hostile,
    )
    rows = title.splitlines()

    assert len(rows) == BATCH_HEADER_ROWS + 1 + 3
    assert not any(contains_control_chars(row) for row in rows)
    assert all(len(row) <= DIALOG_ROW_CHARS for row in rows)
    assert sum(row.startswith("Permission:") for row in rows) == 1
