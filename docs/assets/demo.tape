# VHS tape for docs/assets/demo.gif — https://github.com/charmbracelet/vhs
#
# Reproduce:
#   1. Stage ~/demo with the README extension example as my_ext.py and a small
#      data/train.parquet sample; put `aelix` on PATH; configure provider keys.
#   2. `vhs docs/assets/demo.tape` — writes PNG frames into demoframes/.
#   3. Encode (two-pass palette; vhs's built-in gif encode can be skipped):
#      ffmpeg -y -framerate 15 -start_number 1 -i demoframes/frame-text-%05d.png \
#        -framerate 15 -start_number 1 -i demoframes/frame-cursor-%05d.png \
#        -loop 1 -i demoframes/bar.png -filter_complex \
#        "[2:v][0:v]overlay=30:54:shortest=1[bg];[bg][1:v]overlay=30:54,scale=1000:-1:flags=lanczos,palettegen=max_colors=128" \
#        -frames:v 1 palette.png
#      ffmpeg -y -framerate 15 -start_number 1 -i demoframes/frame-text-%05d.png \
#        -framerate 15 -start_number 1 -i demoframes/frame-cursor-%05d.png \
#        -loop 1 -i demoframes/bar.png -i palette.png -filter_complex \
#        "[2:v][0:v]overlay=30:54:shortest=1[bg];[bg][1:v]overlay=30:54,scale=1000:-1:flags=lanczos[x];[x][3:v]paletteuse=dither=bayer:bayer_scale=5" \
#        -loop 0 demo.gif
#      (bar.png: 1200x680 #171717 with three 14px traffic-light dots at y=17,
#       x=24/46/68 — regenerate with PIL if vhs doesn't emit one.)

Output demoframes/

Set Shell "bash"
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

Type "aelix -e ./my_ext.py --model openrouter/qwen/qwen3.6-35b-a3b"
Sleep 500ms
Enter
Sleep 8s

Type "Use describe_dataset on data/train.parquet and flag columns with nulls. Plain text only."
Sleep 800ms
Enter
Sleep 40s

Type "/model anthropic/claude-haiku-4-5"
Sleep 500ms
Enter
Sleep 4s

Type "Which column needs attention first? Answer in one sentence."
Sleep 800ms
Enter
Sleep 20s

Sleep 3s
