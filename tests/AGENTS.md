# Test Suite Rules & Standards (`tests/`)

1. **Environment & Execution:**
   - Execute tests exclusively using `uv run pytest`.
   - Never run tests directly via unmanaged python, pytest, or pip.

2. **Integrity & Assertions:**
   - Deleting, skipping, or weakening existing assertions is strictly prohibited.
   - Any bug fixes must preserve all regression test guarantees.

3. **Honest Testing (No Over-Mocking):**
   - No `MagicMock` for internal formatters, domain services, or database layers.
   - Always test database interactions against real in-memory SQLite (`Database(":memory:")`).
   - Use lightweight explicit spies (e.g. `DummyBot`) for external Telegram delivery.

4. **Property-Based Testing:**
   - Parsers, formatters, and edge-case invariants must be property-tested with `hypothesis`.
   - Invariants (message length <= 4096, HTML tag balancing, time delta safety) must hold under fuzzing.

5. **Architectural Limits:**
   - Max 400 lines per test file and max 80 lines per function.
   - When adding new test scenarios, decompose into dedicated modules (e.g. `test_properties.py`).
