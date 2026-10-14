#!/bin/bash
# #446 r2 windows sabotage rows (measure branch only), run from the checkout root in Git Bash.
# Each row x3: mutations | expect | grep. The product is e24ea2e8's; the test pristine is the fix;
# BASE swaps the test back to e24ea2e8's (tests/tools/_m446_before.py). Rows that need the
# timeout leg use T_hold1 (attempt 1's python root holds 30 s, announced, alive at the deadline,
# holder inside the job) - deterministic on any runner, no load needed.
PY=${PY:-.venv/Scripts/python.exe}
OUT=out; mkdir -p $OUT
NODE="tests/tools/test_bash_tool_containment.py::test_a_holder_that_never_falls_idle_hits_the_drain_cap[deadline-arm]"
cp tests/tools/test_bash_tool_containment.py tests/tools/test_bash_tool_containment.py.pristine
cp packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py.pristine
export SAB446_BASE_TEST=tests/tools/_m446_before.py
row() {
  name=$1; muts=$2; expect=$3; text=$4
  for rep in 1 2 3; do
    $PY .omc-446/sab446.py . "$muts" > /dev/null || { echo "$name: mutation failed" | tee -a $OUT/sab.txt; return; }
    once="$PWD/$OUT/once-$name-$rep"; echo x > "$once"
    f=$OUT/sab-$name-$rep.txt
    SAB446_ONCE="$once" timeout -k 10 240 $PY -m pytest -p no:cacheprovider -q -rfE -W always::UserWarning "$NODE" > $f 2>&1
    rc=$?
    rm -f "$once"
    got=GREEN; [ $rc -ne 0 ] && got=RED
    hit=yes; if [ -n "$text" ]; then LC_ALL=C grep -a -q -- "$text" $f || hit=no; fi
    verdict=ok; { [ "$got" != "$expect" ] || [ "$hit" != yes ]; } && verdict=UNEXPECTED
    why=$(LC_ALL=C grep -a -m1 -E "^E +(Failed|AssertionError|assert)" $f | cut -c1-330)
    prem=$(LC_ALL=C grep -a -m1 -o "the root had not exited by the caller's [^.]*" $f | cut -c1-260)
    echo "$name#$rep [$muts] expect=$expect got=$got text=$hit :: $(tail -1 $f) :: $verdict :: ${why}${prem}" | tee -a $OUT/sab.txt
  done
}
row W0_none          none                          GREEN ""
row W1_hold          T_hold1                       GREEN "still running when the site's wait gave up"
row W2_base_hold     BASE+T_hold1                  RED   "assert None == 0"
row W3_rootonly      P_rootonly+T_hold1            RED   "the timeout leg's kill left"
row W3w_nokillcheck  P_rootonly+T_hold1+W_nokillcheck GREEN ""
row W4_nokill        P_nokill+T_hold1              RED   "the timeout leg's kill left"
row W5_early         P_early+T_hold1               RED   "before the caller's"
row W6_label         P_label_once                  RED   "never raised"
row W7_noterm        P_noterm                      RED   "the deadline term is gone"
row W8_revert        W_revert+T_hold1              RED   "assert None == 0"
$PY .omc-446/sab446.py . --restore
rm -f tests/tools/test_bash_tool_containment.py.pristine packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py.pristine
git status --short | tee $OUT/status.txt
