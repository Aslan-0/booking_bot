"""Test setup: provide a dummy token so `config.settings` can be imported."""

import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
