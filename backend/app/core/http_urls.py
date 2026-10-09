"""Compatibility import for the single shared http_urls implementation."""
import sys
from elira_common import http_urls as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.http_urls import (
        check_ssrf as check_ssrf,
    )
