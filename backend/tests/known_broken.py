"""
Skip decorator for tests that are pre-existing failures — broken by a
refactor (package rename, mock target moved) but not yet migrated to the
new architecture.

Tests listed in SKIP_TESTS will be reported as "skipped" with a reason,
not "error" or "fail". This keeps the test runner green for the tests
that DO work while making the broken ones visible so they don't get
forgotten.

To unskip a test: fix the underlying issue and remove the entry below.
"""
import unittest

# (test_module_path, test_name, reason) — all three must match exactly.
# Add to this list as broken tests are discovered; remove entries as
# tests are fixed. Anything not listed here is reported normally.
SKIP_TESTS = [
    # No known broken tests remain. Add new entries only when a test
    # fails for a real underlying issue that can't be fixed immediately
    # (architecture-level refactor, missing fixture, etc.). Prefer
    # fixing the test or the code over skipping it.
]


def _mark_skipped(test, reason: str):
    """Return a test instance that will be reported as skipped.

    unittest checks `__unittest_skip__` on the class via
    `getattr(self.__class__, "__unittest_skip__", False)`. Setting the
    flag on the *original* TestCase class would skip every other test
    method on that class (all methods share the class), which is why
    we can't just mutate the class in place. Instead, build a one-off
    subclass per skipped test so the skip is scoped to a single
    instance — the rest of the class runs normally.

    `_FailedTest` is itself a class; for those the runner already
    reports a collection error so we just return the test unchanged
    (the module-level SKIP_TESTS entry is informational in that case).
    """
    # _FailedTest: collection already failed; leave the error in place.
    if isinstance(test, type):
        return test

    # Build a fresh subclass whose name is unique per (test, reason) so
    # unittest's id()/repr don't collide across skips. Inheriting keeps
    # the original test method/attributes intact.
    Skipped = type(
        f"_Skipped_{test.__class__.__name__}_{test._testMethodName}",
        (test.__class__,),
        {
            "__unittest_skip__": True,
            "__unittest_skip_why__": reason,
        },
    )
    # Reconstruct the instance so _testMethodName and other attrs
    # populated by the loader are preserved on the subclass.
    skipped = Skipped.__new__(Skipped)
    skipped.__dict__.update(test.__dict__)
    return skipped


def apply_known_broken_skips(suite: unittest.TestSuite) -> unittest.TestSuite:
    """Walk a TestSuite recursively and wrap SKIP_TESTS in a SkipTest.

    Returns a new suite with the skip wrappers applied. The original suite
    is not modified, so re-applying is safe. Non-leaf nodes (TestSuites)
    are recursed into and rebuilt.
    """
    skip_map = {(module, test): reason for module, test, reason in SKIP_TESTS}
    # Module-level skips: only entries with empty test name apply to whole
    # module. Other entries are scoped to the specific (module, test) above.
    module_skip_map = {module: reason for module, test, reason in SKIP_TESTS if test == ""}
    return _wrap_suite(suite, skip_map, module_skip_map)


def _wrap_suite(node, skip_map, module_skip_map):
    if not isinstance(node, unittest.TestSuite):
        # Leaf test (real TestCase or _FailedTest from a failed import).
        test_id = node.id()

        # _FailedTest ids look like:
        #   "unittest.loader._FailedTest.backend.tests.watchlist.test_watchlist_repository"
        # Normal TestCase ids look like:
        #   "backend.tests.watchlist.test_watchlist_api.TestWatchlistAPI.test_method"
        # We need to strip the class name from TestCase ids to match SKIP_TESTS
        # entries which only contain the module path.
        if test_id.startswith("unittest.loader._FailedTest."):
            # _FailedTest id is "unittest.loader._FailedTest.<module>" (no class).
            # Strip prefix -> "loader._FailedTest.<module>", strip class -> bare module.
            after_prefix = test_id.split(".", 1)[1]
            parts = after_prefix.split(".", 2)  # ["loader", "_FailedTest", "..."]
            module_path = parts[2] if len(parts) > 2 else parts[1]
            method = ""
        elif "." in test_id:
            parts = test_id.rpartition(".")
            # parts == (module.class, ".", "method")
            # module.class might look like "backend.tests.api.TestRouter.test_x"
            # We need to strip the class name: everything up to the last ".".
            module_and_class, _, method = parts
            # Strip the class name (last component before the ".method")
            module_path = module_and_class.rsplit(".", 1)[0]
        else:
            module_path = test_id
            method = ""

        # Try exact module match first (for _FailedTest / module-level skips).
        if module_path in module_skip_map:
            return _mark_skipped(node, module_skip_map[module_path])

        # Normal (module, method) lookup.
        if (module_path, method) in skip_map:
            return _mark_skipped(node, skip_map[(module_path, method)])

        return node

    # TestSuite: rebuild with wrapped children.
    wrapped = unittest.TestSuite()
    for child in node:
        wrapped.addTest(_wrap_suite(child, skip_map, module_skip_map))
    return wrapped
