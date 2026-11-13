"""The museum web app: Flask routes over the shared pipeline (design section 8, E8).

Server-rendered Jinja2, one CSS file, a small submit-and-poll ES module. No
bundler, no Node toolchain. The gallery and every exhibit render fully without
JavaScript; JS is required only for the live paste-and-analyze flow.
"""

from necropsy.web.app import create_app

__all__ = ["create_app"]
