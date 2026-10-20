# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
import os

# Haystack refuses to deserialize classes and callables from modules that are not on its allowlist.
# The test doubles live in ``tests.fakes``; allow them for this test process only.
os.environ.setdefault("HAYSTACK_DESERIALIZATION_ALLOWLIST", "tests")
