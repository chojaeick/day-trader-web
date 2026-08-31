"""USA UI module boundary.

This module intentionally does not import USA runtime/engine/broker code.  The
existing app_v5 USA renderer is injected as a callback so moving the UI behind
this module cannot change the currently connected USA execution path.
"""


def render_us_trading(render_trading):
    return render_trading('USA')
