---
type: regex
pattern: '^\s*(?:[-*>#]+\s*)?(?:\*\*)?(?:title:?\s*)?(?:\*\*)?\s*[\"“]?Show HN:\s*\S'
flags: im
match: not_contains
target: last_message
weight: 2
---
