# Kitchen Display

Pi Zero 2 W driving a 7-colour Inky Impression e-ink panel in the kitchen: glance
(12-hour forecast rail + 7-day agenda), next week, weather. README covers setup.

## Working on the Mac
- Tests: `.venv/bin/python -m pytest -q`.
- New dependency: add it to `requirements.txt`, then `.venv/bin/pip install <pkg>`
  for that package alone — `gpiod`/`spidev` in the full file only build on Linux.
- Preview: `python -m kitchen_display --fixture --view glance --out x.out.png`, then
  `tools/panel_sim.py x.out.png x.sim.out.png`. Judge visuals from the `.sim` image;
  the raw PNG shows colours the panel cannot. New colours join the quantization guard test.

## Rules that aren't visible in the code
- Views receive naive local datetimes; time zones live only in `providers/ics.py`.
- The calendar ICS URL is a secret: it exists only in the gitignored
  `kitchen_display/config.py`. Logs name the feed (`events.ics`), never the URL.
- Specs and plans: `docs/superpowers/specs/` and `docs/superpowers/plans/`.

## On the Pi (user `admin`, checkout `~/kitchen_display`)
- Deploy = push `main`; `run.sh` pulls and reinstalls deps on each daemon start.
- Log: `~/kitchen_display.log`.
- Config is read at startup: after editing it, `systemctl --user restart kitchen-display`
  or hold button D (≥2s). A short D press only redraws.
