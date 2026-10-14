#!/bin/bash
# Verify-pass rows. Run from the tree root. Usage: rows.sh <outdir> [PY]
# Each row: name | expect GREEN/RED | text that must appear (in the pytest output) | env assignments...
OUTD=$1; PY=${2:-"uv run --no-sync python"}
mkdir -p "$OUTD"
NODE="tests/tools/test_v446_probe.py::test_rounds"
TO=$(command -v timeout || command -v gtimeout || true)
if [ -n "$TO" ]; then TO="$TO -k 10 300"; fi
row() {
  name=$1; expect=$2; text=$3; shift 3
  f="$OUTD/$name.txt"; j="$OUTD/$name.jsonl"; rm -f "$j"
  lf="$OUTD/$name.labelcount"; rm -f "$lf"
  extra=()
  for kv in "$@"; do
    if [ "$kv" = "LABEL1" ]; then echo 1 > "$lf"; extra+=("V446_LABEL_FILE=$lf"); else extra+=("$kv"); fi
  done
  env V446_OUT="$j" V446_PARAMS=deadline "${extra[@]}" $TO $PY -m pytest -q -p no:cacheprovider -W always::UserWarning "$NODE" > "$f" 2>&1
  rc=$?
  got=GREEN; [ $rc -ne 0 ] && got=RED
  hit=yes; if [ -n "$text" ]; then LC_ALL=C grep -a -q -F -- "$text" "$f" "$j" 2>/dev/null || hit=no; fi
  verdict=ok; { [ "$got" != "$expect" ] || [ "$hit" != yes ]; } && verdict=UNEXPECTED
  why=$(LC_ALL=C grep -a -m1 -oE "(Failed: .{0,200}|AssertionError: .{0,160}|THE PREMISE FAILED, NOT THE CASE — the root had not exited by the caller's [^:]*: .{0,170})" "$f" | head -1)
  echo "$name [$*] expect=$expect got=$got text=$hit :: $(tail -1 "$f" | cut -c1-80) :: $verdict :: $why" | tee -a "$OUTD/rows.txt"
}
MOD=tests.tools.test_bash_tool_containment
# --- premise shapes on the committed test: GREEN, said
row R0_plain      GREEN ""                                           V446_TESTMOD=$MOD
row R1_held1      GREEN "still running when the site's wait gave up" V446_TESTMOD=$MOD V446_ROOT=held V446_SLOW=1
row R2_pre1       GREEN "before its python child announced"          V446_TESTMOD=$MOD V446_ROOT=pre V446_SLOW=1 V446_HOLD=3
row R3_linger1    GREEN "still running when the site's wait gave up" V446_TESTMOD=$MOD V446_LINGER=1
row R4_heat1      GREEN "a dead heat"                                V446_TESTMOD=$MOD V446_ROOT=held V446_SLOW=1 V446_PRODUCT=heat
row R5_heldall    RED   "every one of 3 attempts"                    V446_TESTMOD=$MOD V446_ROOT=held V446_SLOW=9
row R6_held2      GREEN "THE PREMISE FAILED"                         V446_TESTMOD=$MOD V446_ROOT=held V446_SLOW=2
# --- defect classes on the committed test: RED by name
row D1_early      RED   "before the caller's"                        V446_TESTMOD=$MOD V446_ROOT=held V446_SLOW=1 V446_PRODUCT=early
row D2_unseen     RED   "does not see the root's exit"               V446_TESTMOD=$MOD V446_PRODUCT=unseen V446_PRODUCT_N=1
row D3_rootkill   RED   "(the python root: alive)"                   V446_TESTMOD=$MOD V446_ROOT=held V446_SLOW=1 V446_LINGER=0 V446_PRODUCT=rootkill
row D4_nokill     RED   "(the root: alive)"                          V446_TESTMOD=$MOD V446_ROOT=held V446_SLOW=1 V446_PRODUCT=nokill
row D5_label      RED   "never raised"                               V446_TESTMOD=$MOD LABEL1
row D6_noterm     RED   "the deadline term is gone"                  V446_TESTMOD=$MOD V446_NOTERM=1
row D7_base_held  RED   "assert result.exit_code == 0"                           V446_TESTMOD=tests.tools._v446_base V446_ROOT=held V446_SLOW=1
row D8_base_pre   RED   "never announced"                            V446_TESTMOD=tests.tools._v446_base V446_ROOT=pre V446_SLOW=1 V446_HOLD=3
# --- wrong implementations of the fix: the row that guards each flips
row M1_any_early       GREEN "" V446_TESTMOD=tests.tools.test_v446mut_any V446_ROOT=held V446_SLOW=1 V446_PRODUCT=early
row M1_any_unseen      GREEN "" V446_TESTMOD=tests.tools.test_v446mut_any V446_PRODUCT=unseen V446_PRODUCT_N=1
row M1_any_label       GREEN "" V446_TESTMOD=tests.tools.test_v446mut_any LABEL1
row M2_nokc_rootkill   GREEN "" V446_TESTMOD=tests.tools.test_v446mut_nokillcheck V446_ROOT=held V446_SLOW=1 V446_LINGER=0 V446_PRODUCT=rootkill
row M2_nokc_nokill     GREEN "" V446_TESTMOD=tests.tools.test_v446mut_nokillcheck V446_ROOT=held V446_SLOW=1 V446_PRODUCT=nokill
row M3_alive_heat      RED   "" V446_TESTMOD=tests.tools.test_v446mut_alive V446_ROOT=held V446_SLOW=1 V446_PRODUCT=heat
row M4_anylook_unseen  GREEN "" V446_TESTMOD=tests.tools.test_v446mut_anylook V446_PRODUCT=unseen V446_PRODUCT_N=1
row M5_noearly_early   GREEN "" V446_TESTMOD=tests.tools.test_v446mut_noearly V446_ROOT=held V446_SLOW=1 V446_PRODUCT=early
row M6_slack_early     GREEN "" V446_TESTMOD=tests.tools.test_v446mut_slack V446_ROOT=held V446_SLOW=1 V446_PRODUCT=early
row M7_nolabel_label   GREEN "" V446_TESTMOD=tests.tools.test_v446mut_nolabel LABEL1
row M8_nobranch_held1  RED   "assert None == 0" V446_TESTMOD=tests.tools.test_v446mut_nobranch V446_ROOT=held V446_SLOW=1
