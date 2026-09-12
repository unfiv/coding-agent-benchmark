// Targeted regression test for the "overflow issue when comparing large
// integer types in EXPECT_EQ" task.
//
// This file lives OUTSIDE workspace/googletest on purpose:
//   - it must survive `git reset --hard HEAD && git clean -fd` between runs
//   - it must be outside the agent's writable scope (./workspace/googletest/)
//
// It links against the gtest static libraries that are already built by
// setup.sh (libgtest.a / libgtest_main.a) and includes the (possibly
// agent-modified) public headers from workspace/googletest/googletest/include.
// It does NOT modify or re-run googletest's own test suite - ctest still
// runs separately as the regression net.

#include <cstdint>
#include <limits>

#include "gtest/gtest.h"
#include "gtest/gtest-spi.h"

// EqFailure() (googletest/src/gtest.cc) always renders a genuine EXPECT_EQ
// mismatch as:
//   "Expected equality of these values:\n  <lhs>\n ... \n  <rhs>\n ..."
// That fixed lead-in text is a stable substring to match on - it does not
// depend on variable names or formatting of the printed values.
static const char* const kEqFailureMarker =
    "Expected equality of these values";

// 1) The core overflow trap named in the task: comparing a negative int to
//    a huge uint64_t must NOT be reported as equal. If CmpHelperEQ still
//    does the naive `lhs == rhs`, the signed -1 gets converted to
//    UINT64_MAX by the usual arithmetic conversions, the comparison
//    silently says "equal", EXPECT_EQ never fails, and this
//    EXPECT_NONFATAL_FAILURE fails instead (it got zero failures instead
//    of exactly one).
TEST(ExpectEqOverflowRegression, NegativeOneIsNotUint64Max) {
  const int lhs = -1;
  const uint64_t rhs = std::numeric_limits<uint64_t>::max();
  EXPECT_NONFATAL_FAILURE(EXPECT_EQ(lhs, rhs), kEqFailureMarker);
}

// 2) A second overflow pair with different types/magnitudes, so a fix that
//    is hard-coded to the exact bit pattern of case (1) doesn't pass by
//    coincidence.
TEST(ExpectEqOverflowRegression, NegativeSmallIsNotHugeUnsigned) {
  const int32_t lhs = -2;
  const uint64_t rhs = std::numeric_limits<uint64_t>::max() - 1;
  EXPECT_NONFATAL_FAILURE(EXPECT_EQ(lhs, rhs), kEqFailureMarker);
}

// 3) The "don't overcorrect" guard: a legitimate cross-type equality must
//    still be recognized as equal. This catches a degenerate "fix" that
//    just rejects every mixed-signedness comparison outright.
TEST(ExpectEqOverflowRegression, LargeEqualValuesAcrossSignedness) {
  const int64_t lhs = 123456789012345LL;
  const uint64_t rhs = 123456789012345ULL;
  EXPECT_EQ(lhs, rhs);  // must NOT produce a failure
}

// 4) Same guard as (3) but at small/ordinary magnitudes, so a fix that
//    only special-cases "large" numbers doesn't accidentally break the
//    common case.
TEST(ExpectEqOverflowRegression, OrdinaryEqualValuesAcrossSignedness) {
  const int lhs = 42;
  const uint64_t rhs = 42;
  EXPECT_EQ(lhs, rhs);  // must NOT produce a failure
}