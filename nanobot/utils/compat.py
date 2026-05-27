"""Python version compatibility helpers — used instead of version checks everywhere."""

import sys
from dataclasses import dataclass as _dataclass

# `slots` param for dataclass was added in Python 3.10.
# On 3.9 we silently drop it (no __slots__ optimization, but works fine).
if sys.version_info < (3, 10):

    def dataclass(*args: object, **kwargs: object) -> object:  # type: ignore[misc]
        kwargs.pop("slots", None)
        kwargs.pop("match_args", None)
        kwargs.pop("kw_only", None)
        return _dataclass(*args, **kwargs)  # type: ignore[return-value]
else:
    dataclass = _dataclass
