"""Enable Banking -> Actual Budget connector.

Importing this package applies the actualpy compatibility patch eagerly so any
later `from actual import ...` sees the patched module.
"""
import logging

from bank_connector.actual_patches import patch_actualpy

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

patch_actualpy()
