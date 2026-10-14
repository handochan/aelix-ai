#!/bin/bash
# #446 review round 2 windows sabotage rows (measure branch only), run from the checkout root in Git Bash.
# Each row x3: mutations | expect | grep. The product is e24ea2e8's; the test pristine is the amended fix;
# BASE swaps the test back to e24ea2e8's and R1 to fb5eae28's (.omc-446/test_*.py).
#   T_hold1      attempt 1's python root holds 30 s after announcing (alive at the deadline, holder in the job)
#   T_slowstart1 attempt 1 runs a python that sleeps 8 s BEFORE the real root (pwsh stays the root; the
#                sleeper is a job member that never announces - a python root still starting at the kill)
PY=${PY:-.venv/Scripts/python.exe}
OUT=out; mkdir -p $OUT
NODE="tests/tools/test_bash_tool_containment.py::test_a_holder_that_never_falls_idle_hits_the_drain_cap[deadline-arm]"
cp tests/tools/test_bash_tool_containment.py tests/tools/test_bash_tool_containment.py.pristine
cp packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py.pristine
export SAB446_BASE_TEST=.omc-446/test_e24ea2e8.py SAB446_R1_TEST=.omc-446/test_fb5eae28.py
row() {
  name=$1; muts=$2; expect=$3; text=$4; node=${5:-$NODE}
  for rep in 1 2 3; do
    $PY .omc-446/sab446r2.py . "$muts" > /dev/null || { echo "$name: mutation failed" | tee -a $OUT/sab.txt; return; }
    once="$PWD/$OUT/once-$name-$rep"; echo x > "$once"
    f=$OUT/sab-$name-$rep.txt
    SAB446_ONCE="$once" timeout -k 10 300 $PY -m pytest -p no:cacheprovider -q -rfE -W always::UserWarning "$node" > $f 2>&1
    rc=$?
    rm -f "$once"
    got=GREEN; [ $rc -ne 0 ] && got=RED
    hit=yes; if [ -n "$text" ]; then LC_ALL=C grep -a -q -- "$text" $f || hit=no; fi
    verdict=ok; { [ "$got" != "$expect" ] || [ "$hit" != yes ]; } && verdict=UNEXPECTED
    why=$(LC_ALL=C grep -a -m1 -E "^E +(Failed|AssertionError|assert)" $f | cut -c1-330)
    prem=$(LC_ALL=C grep -a -m1 -o "the root had not exited by the caller's [^.]*" $f | cut -c1-200)
    echo "$name#$rep [$muts] expect=$expect got=$got text=$hit :: $(tail -1 $f) :: $verdict :: ${why}${prem}" | tee -a $OUT/sab.txt
  done
}
row W0_none             none                                GREEN ""
row W1_hold             T_hold1                             GREEN "still running when the site's wait gave up"
row W1b_slowstart       T_slowstart1                        GREEN "before its python child announced its tree"
row W2_base_hold        BASE+T_hold1_old                    RED   "assert None == 0"
row W2b_r1_rootonly_slow R1+P_rootonly+T_slowstart1_old     GREEN ""
row W3_rootonly         P_rootonly+T_hold1                  RED   "was still alive"
row W3w_nokillcheck     P_rootonly+T_hold1+W_nokillcheck    GREEN ""
row W3b_rootonly_slow   P_rootonly+T_slowstart1             RED   "still held"
row W3bw_nogroup        P_rootonly+T_slowstart1+W_nogroup   GREEN ""
row W4_nokill           P_nokill+T_hold1                    RED   "was still alive"
row W5_late             P_late+T_hold1                      RED   "was handed"
row W6_label            P_label_once                        RED   "never raised"
row W7_noterm           P_noterm                            RED   "the deadline term is gone"
row W8_revert           W_revert+T_hold1                    RED   "assert None == 0"
# The tests CI runs, under each wrong version the verify pass listed (and the look schedules the cross
# review mutated): each must turn at least one of them red.
for m in none W_anytimeout W_nokillcheck W_revert W_floor M_signerr M_atonce W_norc; do
  $PY .omc-446/sab446r2.py . "$m" > /dev/null || { echo "ci $m: mutation failed" | tee -a $OUT/ci.txt; continue; }
  f=$OUT/ci-$m.txt
  timeout -k 10 600 $PY -m pytest -p no:cacheprovider -q -rfE -W always::UserWarning tests/tools/test_bash_tool_containment.py -k "drain_cap or 8a_ or 8as_premise or witness_looks or rounding_tie" > $f 2>&1
  echo "ci $m rc=$? :: $(tail -1 $f) :: $(LC_ALL=C grep -a -E '^(FAILED|ERROR) ' $f | sed -E 's/ - .*//' | tr '\n' ' ')" | tee -a $OUT/ci.txt
done
$PY .omc-446/sab446r2.py . --restore
rm -f tests/tools/test_bash_tool_containment.py.pristine packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py.pristine
git status --short | tee $OUT/status.txt
