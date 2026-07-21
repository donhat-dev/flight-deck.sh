def test_zen_evaluate_expression_available():
    import zen

    # Standalone expression evaluation against a context.
    #
    # NOTE: zen-engine==0.53.0's evaluate_expression() resolves context
    # fields as identifiers looked up directly in the ctx dict ("a + b",
    # "a.b", 'a["b"]') -- NOT the JSONPath-style "$.a" the task-1 brief's
    # Step 3 snippet originally used. "$.a + $.b" raises: RuntimeError:
    # {"type":"vmError","source":"Opcode Add: Unsupported type"}; "$" alone
    # is reserved for evaluate_unary_expression's whole-input reference.
    #
    # The plan's actual convention (Global Constraints: context vars
    # "$json", "$node", "$vars", "$run") uses "$json" etc. as a single
    # dollar-prefixed identifier -- a regular ctx dict key, not a JSONPath
    # root. That works exactly as designed, verified here directly so
    # Task 3 doesn't need any evaluator redesign. See
    # .superpowers/sdd/task-1-report.md for the full API probe.
    out = zen.evaluate_expression("$json.a + $json.b", {"$json": {"a": 2, "b": 3}})
    assert out == 5
