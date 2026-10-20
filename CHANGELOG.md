# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - Unreleased

### Added

- `ACGSConfirmationStrategy`, a Haystack Human-in-the-Loop `ConfirmationStrategy` for use with the native
  `ConfirmationHook` at the `before_tool` hook point.
- `format_tool_call`, the documented text representation of a tool call that ACGS rules are evaluated against.
- Fail-closed handling: errors during evaluation or review reject the tool call.
- Optional `review_strategy` for rules that require human review; modified parameters are evaluated again.
- `to_dict` / `from_dict` serialization.
