"""Settings: the bundled defaults, with kitchen_display/config.py laid over them.

config.py is optional and may set only the keys it wants to change.
"""
from .config_example import config as DEFAULTS


def load():
    cfg = dict(DEFAULTS)
    try:
        from .config import config as overrides
    except ImportError:
        overrides = {}
    cfg.update(overrides)
    return cfg
