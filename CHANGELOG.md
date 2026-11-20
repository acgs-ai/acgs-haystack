# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## 0.2.0 (unreleased)

### Added

- Offline `examples/deny_and_audit.py`: one allowed and one denied Haystack tool call, with an application
  decision trail.
- Compatibility coverage for nested JSON-native arguments and bytes/bytearray formatting with acgs-lite 4.0.0.

### Changed

- Document the compatibility matrix: the default/test Hatch environments resolve acgs-lite 4.0.0, and the CI
  `test-min` job tests the 3.0.1 floor. Runtime support remains `acgs-lite>=3.0.1,<5`.
- Clarify that the adapter validates formatted text and does not issue signed receipts.

## [0.1.1] - 2026-10-20

### Fixed

- When a blocking rule and a review rule both match a tool call, the denial reason now lists only the blocking
  rules after "Blocked by" and names the matched review rules separately ("Review rules that also matched: ...").
  Previously the review rule was also listed under "Blocked by". The verdict was already `deny` and is unchanged;
  `rule_ids` still contains every matched rule.

## [0.1.0] - 2026-10-19

### Added

- `ACGSConfirmationStrategy`, a Haystack Human-in-the-Loop `ConfirmationStrategy` for use with the native
  `ConfirmationHook` at the `before_tool` hook point.
- `format_tool_call`, the documented text representation of a tool call that ACGS rules are evaluated against.
- Fail-closed handling: errors during evaluation or review reject the tool call.
- Optional `review_strategy` for rules that require human review; modified parameters are evaluated again.
- `to_dict` / `from_dict` serialization.
