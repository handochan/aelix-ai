#!/bin/bash
# #446 r2verify sabotage rows (measure branch only), from the checkout root, in bash (Git Bash on windows).
# Product = e24ea2e8; test file = 03cb1d03's. Mutants: .omc-446v/mutate.py (text) and v446_plug.py (runtime).
PY=${PY:-.venv/bin/python}
[ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
OUT=out; mkdir -p $OUT
F=tests/tools/test_bash_tool_containment.py
NODE="$F::test_a_holder_that_never_falls_idle_hits_the_drain_cap[deadline-arm]"
CIROW="$F::test_8a_runs_again_after_a_root_that_had_not_exited_by_its_deadline"
cp $F .omc-446v/pristine.py
export PYTHONPATH="$PWD/.omc-446v"
export V446_LOG="$PWD/$OUT/v446.log"

# 1. the tests CI runs, under each wrong version: every one but 'restore' must turn one red
for m in restore W_anytimeout W_nokillcheck W_revert W_floor W_norc M_signerr M_atonce M_half; do
  $PY .omc-446v/mutate.py $m > /dev/null || { echo "ci $m: mutation failed" | tee -a $OUT/ci.txt; continue; }
  f=$OUT/ci-$m.txt
  timeout -k 10 600 $PY -m pytest -p no:cacheprovider -q -rfE -W always::UserWarning $F -k "drain_cap or 8a_ or 8as_premise or witness_looks or rounding_tie" > $f 2>&1
  echo "ci $m rc=$? :: $(tail -1 $f) :: $(LC_ALL=C grep -a -E '^(FAILED|ERROR) ' $f | sed -E 's/ - .*//; s/.*:://' | tr '\n' ' ')" | tee -a $OUT/ci.txt
done
$PY .omc-446v/mutate.py restore > /dev/null

# 2. runtime rows: name | muts | shell spec (POSIX probe shell only) | node | expect | text
row() {
  name=$1; muts=$2; spec=$3; node=$4; expect=$5; text=$6
  for rep in 1 2; do
    f=$OUT/row-$name-$rep.txt
    once="$PWD/$OUT/once-$name-$rep"; rm -f "$once"
    shellvar=()
    if [ "$spec" != "-" ]; then echo "$spec" > "$once"; shellvar=(SHELL="$PWD/.omc-446v/vshell"); fi
    env "${shellvar[@]}" V446_MUT="$muts" V446_ONCE="$once" timeout -k 10 300 $PY -m pytest -p no:cacheprovider -p v446_plug -q -rfE -W always::UserWarning "$node" > $f 2>&1
    rc=$?
    rm -f "$once"
    got=GREEN; [ $rc -ne 0 ] && got=RED
    hit=yes; if [ -n "$text" ]; then LC_ALL=C grep -a -q -- "$text" $f || hit=no; fi
    verdict=ok; { [ "$got" != "$expect" ] || [ "$hit" != yes ]; } && verdict=UNEXPECTED
    why=$(LC_ALL=C grep -a -m1 -oE "(Failed: .{0,250}|THE PREMISE FAILED.{0,200}|E +assert .{0,100})" $f)
    echo "$name#$rep [$muts | $spec] expect=$expect got=$got text=$hit :: $(tail -1 $f) :: $verdict :: $why" | tee -a $OUT/rows.txt
  done
}
# both platforms: the CI premise row under wrong timeout legs and waits, and the late-start probe
WIN=no; case "$(uname -s)" in MINGW*|MSYS*) WIN=yes;; esac
# POSIX: the root execs python, so its group IS the tree and a root-only kill ends it (GREEN, the premise);
# win32: pwsh + its python child + the holder in the job, so a root-only kill leaves the python root (RED).
if [ $WIN = yes ]; then
  row C1_cirow_rootonly end:rootonly        - "$CIROW" RED   "the timeout leg's kill missed"
else
  row C1_cirow_rootonly end:rootonly        - "$CIROW" GREEN "the root ended by the kill"
fi
row C2_cirow_nokill    end:nokill           - "$CIROW" RED   "the timeout leg's kill missed"
row C3_cirow_short     site:short           - "$CIROW" RED   "was handed"
row C4_cirow_late1     site:latestart_all   - "$CIROW" GREEN "still running when the site's wait gave up"
row C5_8a_blind        wait:blind           - "$NODE"  RED   "does not see the root's exit"
if [ $WIN = no ]; then
  # POSIX: the cross rows with the probe shell (first root late / a slow-starting python child)
  row P1_real_pre       -             "pre 1.3"       "$NODE" GREEN "still running when the site's wait gave up"
  row P2_nokill_pre     end:nokill    "pre 1.3"       "$NODE" RED   "exited 0 on its own"
  row P3_nokill_post    end:nokill    "post 1.3"      "$NODE" RED   "exited 0 on its own"
  row P4_rootonly_pre   end:rootonly  "pre 1.3"       "$NODE" GREEN "the root ended by the kill"
  row P6_rootonly_slow  end:rootonly  "childslow 1.3" "$NODE" RED   "after the timeout leg's kill went out"
  row P7_nokill_slow    end:nokill    "childslow 1.3" "$NODE" RED   "exited 0 on its own"
  row P9_rootonly_slow6 end:rootonly  "childslow 6.0" "$NODE" RED   "process group still held"
  row P10_blind_signerr wait:blind,look:signerr "pre 0.5" "$NODE" RED "too early to place"
  row P11_short_pre     site:short    "pre 1.3"       "$NODE" RED   "was handed"
  row P12_latestart     site:latestart "pre 2.5"      "$NODE" GREEN "still running when the site's wait gave up"
fi
rm -f .omc-446v/pristine.py
git status --short | tee $OUT/status.txt
