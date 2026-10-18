List every distinct behavior in this requirement that a tester could check with its own test scenario.
Include the normal case, each boundary, each error, and each rule that something does not change.
One short sentence per behavior. Do not repeat a behavior in other words, and do not add behaviors the
requirement does not state. Do not list statements about how tests are set up or about the environment
(for example that the schema is frozen, that no trigger is needed, or that tests roll back): those are
preconditions, not behaviors. List at most {max} behaviors.

REQUIREMENT: {text}

Return JSON: {"aspects": ["<behavior 1>", "<behavior 2>"]}
