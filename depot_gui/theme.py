"""Layout numbers. Not settings: a project does not choose a table's row height.

Kept apart from Settings on purpose. These used to sit mixed in with a
project's type colours, and the file gave no way to tell which lines described
the project and which described the interface.
"""

NODE_WIDTH = 128
NODE_HEIGHT = 54
SECONDARY_NODE_WIDTH = 116
SECONDARY_NODE_HEIGHT = 34
LAYER_SPACING = 200
NODE_SPACING = 100
TOOLTIP_MAX_WIDTH = 320
SPLITTER_DEFAULT = 60

# The thin bar every page above the graph wears: title on the left, that
# page's service buttons on the right.
#
# The buttons in it are dense because of this number: a round QBtn at size=sm
# is 3em of a 10px font, which is 30px, and it would poke through a 28px bar.
# Dense takes it to 2.4em — 24px, and the bar keeps a couple of pixels around
# it. Raising the bar again is what lets that prop go.
HEADER_HEIGHT = 28

TABLE_DEFAULT_PAGE_SIZE = 16
TABLE_PAGE_SIZE_OPTIONS = [5, 10, 25, 50, 0]
TABLE_COLUMN_MIN_WIDTH = 40
TABLE_COLUMN_MAX_WIDTH = 320
TABLE_ROW_HEIGHT = 32

RUNNING_BORDER_COLOR = "#22C55E"
RUNNING_BORDER_WIDTH = 4
ERROR_COLOR = "#FF0000"
