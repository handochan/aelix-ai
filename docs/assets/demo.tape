# VHS tape for docs/assets/demo.gif — https://github.com/charmbracelet/vhs
#
# Scenario: the agent WRITES ITS OWN EXTENSION — reads extension_guide.md,
# authors a pandas describe_dataset tool into my_ext.py (auto-edit permission
# mode), /reload hot-loads it without restarting, and the next prompt runs it
# in-process.
#
# Reproduce:
#   1. Stage ~/demo with: a stub my_ext.py (`def setup(aelix): pass`), an
#      extension_guide.md holding the single-file extension example from the
#      docs, and a small data/train.parquet; `aelix` on PATH; provider keys.
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

Hide
Type "cd ~/demo && clear"
Enter
Sleep 1s
Show

Type "aelix -e ./my_ext.py --model anthropic/claude-haiku-4-5"
Sleep 500ms
Enter
Sleep 8s

Type "/permissions cycle"
Enter
Sleep 2s

Type "Read extension_guide.md, then rewrite my_ext.py: add a describe_dataset tool that takes a Parquet path and returns pandas df.describe().to_markdown(). No shell commands."
Sleep 800ms
Enter
Sleep 65s

Type "/reload"
Enter
Sleep 4s

Type "Use the describe_dataset tool on data/train.parquet and flag columns with nulls. Plain text, no shell commands."
Sleep 800ms
Enter
Sleep 32s

Sleep 3s
