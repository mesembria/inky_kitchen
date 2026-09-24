"""Load kitchen_display/config.py, falling back to the bundled defaults."""


def load():
    try:
        from .config import config
    except ImportError:
        from .config_example import config
    return dict(config)
