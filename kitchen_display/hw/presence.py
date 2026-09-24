"""Room occupancy. Core ships the stub; the mmWave slice replaces it.

Not PIR: a PIR reads a still person as 'empty' and flashes in their face.
"""


class AlwaysEmpty:
    """The Core stub. With it, the gate's occupancy rules collapse to
    'refresh hourly' — exactly the weather app's old behaviour."""

    def occupied(self):
        return False

    def start(self, queue):
        return None
