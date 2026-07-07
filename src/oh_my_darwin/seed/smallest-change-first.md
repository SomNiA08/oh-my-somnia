---
id: smallest-change-first
title: Prefer the smallest change that satisfies the task
status: active
origin: seed
born: 2026-07-07
uses: 0
wins: 0
---
Do the simplest thing that fully satisfies the task and its success criteria.
Don't add features, abstractions, config options, or defensive error handling
that were not asked for. A smaller diff is easier to verify and less likely to
break the fitness check.
