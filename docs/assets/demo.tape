# VHS tape for docs/assets/demo.gif — https://github.com/charmbracelet/vhs
#
# Scenario: the agent WRITES ITS OWN EXTENSION — reads extension_guide.md,
# authors a DuckDB `duckdb_query` tool into my_ext.py (auto-edit permission
# mode), /reload hot-loads it without restarting, and the next prompt runs it
# in-process against a Parquet file. The recording OPENS ON AELIX'S STARTUP
# SCREEN: launching it is done inside a Hide block, so the first frame a reader
# sees is the banner, not a shell prompt.
#
# Reproduce:
#   1. Stage ~/demo with: a stub my_ext.py (`def setup(aelix): pass`), an
#      extension_guide.md holding the single-file extension example from the
#      docs, and data/orders.parquet (write it with DuckDB itself — see
#      docs/assets/make_demo_fixture.py). `aelix` on PATH; provider key in env.
#      The example needs only `duckdb`; no pandas, no pyarrow.
#   2. Fonts: CaskaydiaCove Nerd Font Mono (nerd-fonts CascadiaCode.zip) +
#      fonts-noto-color-emoji (statusline 📂 falls back to it), fc-cache -f.
#   3. `vhs docs/assets/demo.tape` — writes PNG frames into demoframes/
#      (this environment's vhs-internal gif encode is skipped on purpose).
#   4. Fast-forward idle: `python docs/assets/dedup_frames.py demoframes seq`
#      collapses runs of byte-identical frame-text PNGs (cursor is a separate
#      overlay stream, so true idle dedups cleanly); extend the last frame
#      ~45 links for a 3s closing hold.
#   5. bar.png: 1200x680 #171717 + three 14px traffic-light dots (PIL) in seq/.
#   6. Encode (two-pass palette):
#      ffmpeg -y -framerate 15 -start_number 1 -i seq/frame-text-%05d.png \
#        -framerate 15 -start_number 1 -i seq/frame-cursor-%05d.png \
#        -loop 1 -i seq/bar.png -filter_complex \
#        "[2:v][0:v]overlay=30:54:shortest=1[bg];[bg][1:v]overlay=30:54,scale=1000:-1:flags=lanczos,palettegen=max_colors=128" \
#        -frames:v 1 palette.png
#      ffmpeg -y -framerate 15 -start_number 1 -i seq/frame-text-%05d.png \
#        -framerate 15 -start_number 1 -i seq/frame-cursor-%05d.png \
#        -loop 1 -i seq/bar.png -i palette.png -filter_complex \
#        "[2:v][0:v]overlay=30:54:shortest=1[bg];[bg][1:v]overlay=30:54,scale=1000:-1:flags=lanczos[x];[x][3:v]paletteuse=dither=bayer:bayer_scale=5" \
#        -loop 0 demo.gif

Output demoframes/

Set Shell "bash"
Set FontFamily "CaskaydiaCove Nerd Font Mono"
Set FontSize 15
Set Width 1200
Set Height 680
Set Padding 14
Set WindowBar Colorful
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Framerate 15
Set PlaybackSpeed 1.5

# Launch inside Hide so the first visible frame is Aelix's startup screen. The
# `clear &&` matters: without it the shell's echo of the launch line survives
# above the banner and the recording opens on a command, not on Aelix.
Hide
Type "cd ~/demo && clear"
Enter
Sleep 1s
Type "clear && aelix -e ./my_ext.py --model anthropic/claude-haiku-4-5"
Enter
Sleep 45s
Show

Sleep 2s

Type "/permissions cycle"
Enter
Sleep 2s

Type "Read extension_guide.md, then rewrite my_ext.py: add a duckdb_query tool taking one sql string. Call duckdb.sql(sql) directly — DuckDB reads Parquet in place, so register no tables — and return str() of the relation so DuckDB's own table formatting survives. No shell commands. Be brief."
Sleep 800ms
Enter
Sleep 70s

Type "/reload"
Enter
Sleep 4s

Type "Use duckdb_query on data/orders.parquet: rows per channel, and how many of those rows have a churn_score. No shell commands."
Sleep 800ms
Enter
Sleep 35s

Sleep 3s
