"""Defaults. Copy to kitchen_display/config.py to override; config.py is
gitignored.

Timings live here rather than in code so they can be tuned on the wall
without a redeploy.
"""
config = {
    "location_name": "Lafayette, CO",
    "refresh_interval_s": 60 * 60,        # base cadence: hourly
    "staleness_ceiling_s": 3 * 60 * 60,   # refresh even if occupied past this
    "long_press_s": 2.0,
    "debounce_s": 0.2,
    "daily_restart_hour": 4,              # exit 0 at ~04:00 so run.sh pulls updates
    "timezone": "America/Denver",
    # Secrets — set these in config.py, never here. Unset leaves the block empty.
    "calendar_ics_url": None,   # Google Calendar: Settings → the calendar → Secret address in iCal format
    "meals_ics_url": None,      # AnyList: Meal Plan → Settings → calendar feed URL
    "ics_max_age_s": 24 * 60 * 60,   # use the last good copy this long when a fetch fails
}
