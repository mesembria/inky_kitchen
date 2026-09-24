"""Output targets for a rendered image.

The Inky Impression is the real target; the other two exist so the whole
daemon can run on a development machine with no panel attached.
"""


class NullPanel:
    """Records what it was shown. Used by tests and by --simulate."""

    def __init__(self):
        self.shown = []

    def show(self, img):
        self.shown.append(img)


class PngPanel:
    """Writes each frame to a PNG path. Used by --out."""

    def __init__(self, path):
        self.path = path

    def show(self, img):
        img.save(self.path)


class InkyPanel:
    """The real 7-colour panel.

    `inky` needs Pi-only GPIO libraries, so it is imported inside show() —
    constructing an InkyPanel on a Mac must not raise.
    """

    def __init__(self):
        self._display = None

    def show(self, img):
        if self._display is None:
            from inky.auto import auto
            self._display = auto()
        self._display.set_image(img.convert("RGB"))
        self._display.show()
