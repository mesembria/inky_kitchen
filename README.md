# Kitchen Display

A wall dashboard for the kitchen on a Pimoroni Inky Impression 7.3". The resting
view shows the weather now and for the next few hours, the family's week, and
what's for dinner. The rear buttons switch views. It refreshes hourly, holds the
refresh while someone is in the room (once the presence sensor lands), and never
shows a blank or silently wrong panel: a failed fetch re-shows the last good image
marked STALE.

Forked from the Inky Impression weather display, which lives on here as the
weather view (`inky_weather/`, unchanged). Design and plans are in
`docs/superpowers/`.

## Hardware
- Raspberry Pi (Pi 4 for dev, Pi Zero 2W recommended for the final install — needs a pre-soldered header)
- Pimoroni Inky Impression 7.3" (mounts on the 40-pin GPIO header)

## Setup (on the Pi)
1. Enable SPI and I2C: `sudo raspi-config` → Interface Options.
2. Free the SPI chip-select pin for the Inky library. On current Raspberry Pi OS
   (Bookworm), the kernel SPI driver claims GPIO8, and the `inky` library fails
   with `Chip Select: (line 8, GPIO8) currently claimed by spi0 CS0`. Add the
   `spi0-0cs` overlay (enables SPI0 with no kernel-managed chip-select lines) to
   `/boot/firmware/config.txt` (older images: `/boot/config.txt`), right after the
   existing `dtparam=spi=on` line — keep both:
   ```
   dtparam=spi=on
   dtoverlay=spi0-0cs
   ```
   Then `sudo reboot`.
3. Clone the repo and create a virtualenv:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. Configure:
   ```bash
   cp inky_weather/config.example.py inky_weather/config.py
   # edit config.py: google_weather_key, lat, long, location_name
   ```

## Configuration

- `inky_weather/config.py` — Google Weather key, lat/long, location name
  (copy from `inky_weather/config.example.py`).
- `kitchen_display/config.py` — optional; overrides the refresh timings in
  `kitchen_display/config_example.py`.

Both are gitignored.

## Run it

Development, on a Mac:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install Pillow requests pytest        # NOT inky — it needs Pi-only GPIO libs
python3 -m pytest -q
python3 -m kitchen_display --fixture --view glance --out glance.png
python3 tools/panel_sim.py glance.png panel_sim.png   # review THIS one
```

`--view` takes `glance`, `weather`, or `nextweek`. Drive the whole loop without
hardware — `a/b/c/d` are short presses, `A/B/C/D` long ones:

```bash
python3 -m kitchen_display --fixture --simulate -v
```

On the Pi, systemd runs the daemon and `run.sh` self-updates on every start.

**If this Pi ran the weather display, remove its hourly cron line first** —
otherwise the old app and the daemon both drive the panel (two flashes an hour,
alternating views, and contention for SPI and GPIO):

```bash
crontab -e        # delete the line ending in inky_weather_odin/run.sh ...
crontab -l        # confirm it's gone
```

Then install the service:

```bash
mkdir -p ~/.config/systemd/user
cp kitchen-display.service ~/.config/systemd/user/
systemctl --user enable --now kitchen-display
sudo loginctl enable-linger $USER
```

Logs go to `~/kitchen_display.log`, one line per render.

Code updates land on the next restart — the daily 04:00 restart, a long press
on button D, or `systemctl --user restart kitchen-display`. The first two re-run
`run.sh` in place rather than exiting, so they never count against systemd's
crash limit. The version string in
the panel header is how you confirm an update landed.

### Auto-update

Each start, `run.sh`:
1. Waits up to 90s for the clock to sync (a Pi Zero has no real-time clock, so
   after a power cut the date is wrong until NTP catches up).
2. `git fetch` + `git reset --hard origin/main` — the Pi always matches the latest
   `main`. Config files and the cached image are gitignored, so the reset never
   touches them.
3. Reinstalls dependencies only if `requirements.txt` changed in that update.
4. If the network is down (fetch fails), it skips the update and runs the code
   already on disk — the display never goes dark over a failed pull.

A commit that starts and then crashes is retried 5 times in 10 minutes, then
systemd stops trying; the panel keeps its last image. Fix it over SSH, then
`systemctl --user reset-failed kitchen-display`.

## Buttons

| Button | Short press | Long press (2s) |
|---|---|---|
| A | Glance — back home | Next week's agenda |
| B | Weather view | *(historical weather, later)* |
| C | — | — |
| D | Redraw now | Pull and restart |

The glance is the resting view; a scheduled refresh always returns to it.

### Versioning

The running version is `git describe --tags --always --dirty` — the short commit
SHA until you tag, then the tag name. It appears in the log line above and in the
top-right of the panel header (after the "updated" time). To cut a named release:
```bash
git tag v1.0.0 && git push --tags
```
After that the panel and log show `v1.0.0` (or e.g. `v1.0.0-3-gabc1234` three
commits later).

## Designing for the panel

### The PNG lies: design against the 7-colour palette

**A `--out` PNG is not what the panel shows.** `main.py` hands a full RGB image to
the `inky` library, which quantizes it to the panel's seven colours — black, white,
red, green, blue, yellow, orange. There is **no gray**. Any near-white tone snaps to
pure white and disappears on the hardware while looking perfect in the PNG.

This has already shipped a bug once: the precip reference lines used a faint gray
`(230, 231, 236)`, rendered correctly in every local preview and every test, and were
invisible on the device.

Rules for anything you draw:

- **Never encode meaning in lightness.** A "faint" or "subtle" element cannot be a
  pale colour. Get faintness from *coverage* instead — see `_dotted_line()` in
  `render.py`, which draws one pixel every three so a full-ink rule reads light.
- **Check any new colour constant against the palette** before using it. Add it to
  the guard in `test_gridlines_survive_seven_color_quantization`
  (`tests/test_smoke.py`) (or, for the kitchen views, to `GLANCE_COLORS` / `AGENDA_COLORS`) so a
  non-surviving colour fails the suite rather than shipping to the panel.
- **Known-invisible constants** still in `render.py`: `FAINT = (225, 226, 230)` and
  `COLOR_DRY = (210, 210, 210)`. Both quantize to white. Do not use them for
  anything load-bearing.

To preview what the panel will actually display, quantize the render:

```bash
python3 -m kitchen_display --fixture --view glance --out preview.png
python3 tools/panel_sim.py preview.png panel_sim.png
```

Review `panel_sim.png`, not `preview.png`, when judging whether a visual change works.
