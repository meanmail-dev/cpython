import ast
import dis
import io
import sys
import tokenize
import unittest
from types import SimpleNamespace
from typing import Any


class NoneAwareTests(unittest.TestCase):
    def evaluate(self, source: str, **namespace: Any) -> Any:
        return eval(source, namespace)

    def test_attribute(self) -> None:
        self.assertIsNone(self.evaluate("value?.name", value=None))
        self.assertEqual(
            self.evaluate("value?.name", value=SimpleNamespace(name="Ada")),
            "Ada",
        )

    def test_only_none_short_circuits(self) -> None:
        for value in (0, False, "", [], {}):
            with self.subTest(value=value):
                self.assertIs(
                    self.evaluate("value?.__class__", value=value), type(value)
                )

    def test_does_not_test_truthiness(self) -> None:
        class Value:
            name = "Ada"

            def __bool__(self) -> bool:
                raise AssertionError("Truthiness must not be evaluated")

        self.assertEqual(self.evaluate("value?.name", value=Value()), "Ada")

    def test_base_evaluated_once(self) -> None:
        calls = []

        def make() -> SimpleNamespace:
            calls.append(1)
            return SimpleNamespace(name="Ada")

        self.assertEqual(self.evaluate("make()?.name", make=make), "Ada")
        self.assertEqual(calls, [1])

    def test_local_access_uses_fused_load(self) -> None:
        namespace: dict[str, Any] = {"Any": Any}
        exec(
            "def optional(value: Any) -> Any:\n"
            "    return value?.name\n"
            "def explicit(value: Any) -> Any:\n"
            "    return value.name if value is not None else None\n",
            namespace,
        )
        optional = namespace["optional"]
        explicit = namespace["explicit"]
        self.assertIn(
            "LOAD_FAST_LOAD_FAST",
            [
                instruction.opname
                for instruction in dis.get_instructions(optional)
            ],
        )
        self.assertNotIn(
            "COPY",
            [
                instruction.opname
                for instruction in dis.get_instructions(optional)
            ],
        )
        for value in (None, SimpleNamespace(name="Ada")):
            self.assertEqual(optional(value), explicit(value))

    def test_traced_local_rebinding_preserves_operand(self) -> None:
        namespace: dict[str, Any] = {"Any": Any}
        exec(
            "def access(value: Any) -> Any:\n    return value?.name\n",
            namespace,
        )
        access = namespace["access"]
        check_offset = next(
            instruction.offset
            for instruction in dis.get_instructions(access)
            if instruction.opname == "POP_JUMP_IF_NONE"
        )

        def trace(frame: Any, event: str, argument: Any) -> Any:
            if frame.f_code is access.__code__:
                frame.f_trace_opcodes = True
                if event == "opcode" and frame.f_lasti == check_offset:
                    frame.f_locals["value"] = None
            return trace

        previous_trace = sys.gettrace()
        sys.settrace(trace)
        try:
            self.assertEqual(access(SimpleNamespace(name="Ada")), "Ada")
        finally:
            sys.settrace(previous_trace)

    def test_local_access_outside_fused_load_range(self) -> None:
        namespace: dict[str, Any] = {"Any": Any}
        parameters = [f"arg{index}: Any" for index in range(16)]
        parameters.append("value: Any")
        exec(
            f"def access({', '.join(parameters)}) -> Any:\n"
            "    return value?.name\n",
            namespace,
        )
        access = namespace["access"]
        self.assertIn(
            "COPY",
            [
                instruction.opname
                for instruction in dis.get_instructions(access)
            ],
        )
        for value, expected in (
            (None, None),
            (SimpleNamespace(name="Ada"), "Ada"),
        ):
            self.assertEqual(access(*([None] * 16), value), expected)

    def test_local_access_in_expression_and_chain(self) -> None:
        namespace: dict[str, Any] = {"Any": Any}
        exec(
            "def access(value: Any) -> Any:\n"
            "    result = ('prefix', value?.profile?.name)\n"
            "    return result\n",
            namespace,
        )
        for value, expected in (
            (None, None),
            (SimpleNamespace(profile=None), None),
            (SimpleNamespace(profile=SimpleNamespace(name="Ada")), "Ada"),
        ):
            self.assertEqual(namespace["access"](value), ("prefix", expected))

    def test_unbound_local_access(self) -> None:
        namespace: dict[str, Any] = {"Any": Any}
        exec(
            "def access() -> Any:\n"
            "    result = value?.name\n"
            "    value = None\n"
            "    return result\n",
            namespace,
        )
        with self.assertRaises(UnboundLocalError):
            namespace["access"]()

    def test_dynamic_name_lookup_once(self) -> None:
        class Namespace(dict[str, Any]):
            def __getitem__(self, key: str) -> Any:
                if key == "value":
                    calls.append(key)
                    return SimpleNamespace(name="Ada")
                return super().__getitem__(key)

        calls: list[str] = []
        self.assertEqual(eval("value?.name", {}, Namespace()), "Ada")
        self.assertEqual(calls, ["value"])

    def test_chain(self) -> None:
        source = "value?.profile.name.upper()"
        self.assertIsNone(self.evaluate(source, value=None))
        self.assertEqual(
            self.evaluate(
                source,
                value=SimpleNamespace(profile=SimpleNamespace(name="Ada")),
            ),
            "ADA",
        )

    def test_multiple_checks(self) -> None:
        source = "value?.profile?.name"
        for value in (None, SimpleNamespace(profile=None)):
            with self.subTest(value=value):
                self.assertIsNone(self.evaluate(source, value=value))
        self.assertEqual(
            self.evaluate(
                source,
                value=SimpleNamespace(profile=SimpleNamespace(name="Ada")),
            ),
            "Ada",
        )

    def test_unchecked_none_still_raises(self) -> None:
        with self.assertRaises(AttributeError):
            self.evaluate(
                "value?.profile.name", value=SimpleNamespace(profile=None)
            )

    def test_missing_attribute_still_raises(self) -> None:
        with self.assertRaises(AttributeError):
            self.evaluate("value?.missing", value=object())

    def test_property_error_still_raises(self) -> None:
        class Value:
            @property
            def name(self) -> str:
                raise ValueError("Property failed")

        with self.assertRaisesRegex(ValueError, "Property failed"):
            self.evaluate("value?.name", value=Value())

    def test_call_arguments_skipped(self) -> None:
        def fail() -> None:
            raise AssertionError("Argument must not be evaluated")

        for source in (
            "value?.method(fail())",
            "value?.method(key=fail())",
            "value?.method(*fail(), **fail())",
        ):
            with self.subTest(source=source):
                self.assertIsNone(self.evaluate(source, value=None, fail=fail))

    def test_call_arguments_evaluated(self) -> None:
        value = SimpleNamespace(method=lambda *args, **kwargs: (args, kwargs))
        self.assertEqual(
            self.evaluate(
                "value?.method(1, *[2], key=3, **{'other': 4})", value=value
            ),
            ((1, 2), {"key": 3, "other": 4}),
        )

    def test_subscript_tail(self) -> None:
        self.assertIsNone(self.evaluate("value?.items[missing]", value=None))
        self.assertEqual(
            self.evaluate(
                "value?.items[1]", value=SimpleNamespace(items=[1, 2])
            ),
            2,
        )
        self.assertEqual(
            self.evaluate(
                "value?.items[1:]", value=SimpleNamespace(items=[1, 2, 3])
            ),
            [2, 3],
        )

    def test_parentheses_end_chain(self) -> None:
        with self.assertRaises(AttributeError):
            self.evaluate("(value?.profile).name", value=None)
        with self.assertRaises(TypeError):
            self.evaluate("(value?.method)(missing)", value=None, missing=1)
        self.assertIsNone(self.evaluate("(value?.profile)?.name", value=None))

    def test_nested_argument_is_independent(self) -> None:
        self.assertEqual(
            self.evaluate(
                "identity(value?.name).real", value=None, identity=lambda x: 5
            ),
            5,
        )

    def test_boolean_and_conditional_context(self) -> None:
        self.assertEqual(
            self.evaluate("value?.name or 'fallback'", value=None), "fallback"
        )
        self.assertEqual(
            self.evaluate("1 if value?.name else 2", value=None), 2
        )
        self.assertTrue(self.evaluate("value?.name is None", value=None))

    def test_lambda_and_comprehension(self) -> None:
        self.assertEqual(
            self.evaluate(
                "[value?.name for value in values]",
                values=[None, SimpleNamespace(name="Ada")],
            ),
            [None, "Ada"],
        )
        self.assertIsNone(self.evaluate("(lambda value: value?.name)(None)"))

    def test_fstring(self) -> None:
        self.assertEqual(self.evaluate("f'{value?.name}'", value=None), "None")

    def test_no_temporary_names(self) -> None:
        namespace = {"value": SimpleNamespace(name="Ada"), "__builtins__": {}}
        self.assertEqual(eval("value?.name", namespace), "Ada")
        self.assertEqual(set(namespace), {"value", "__builtins__"})

    def test_invalid_targets(self) -> None:
        for source in (
            "value?.name = 1",
            "value?.name += 1",
            "value?.profile.name = 1",
            "value?.items[0] = 1",
            "del value?.name",
            "del value?.items[0]",
            "for value?.name in []: pass",
            "value?.name: int = 1",
        ):
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                compile(source, "<test>", "exec")

    def test_other_question_mark_operators_rejected(self) -> None:
        for source in ("value?", "value?[0]", "value?()", "value ?? 1"):
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                compile(source, "<test>", "eval")

    def test_ast_roundtrip(self) -> None:
        for source in (
            "value?.name",
            "value?.profile.name.upper()",
            "(value?.profile).name",
            "value?.items[key?.name]",
            "value?.profile?.name",
        ):
            with self.subTest(source=source):
                tree = ast.parse(source, mode="eval")
                self.assertIsInstance(
                    tree.body,
                    ast.OptionalChain
                    if not source.startswith("(")
                    else ast.Attribute,
                )
                self.assertEqual(
                    ast.dump(ast.parse(ast.unparse(tree), mode="eval")),
                    ast.dump(tree),
                )
                compile(tree, "<ast>", "eval")

    def test_tokenize_roundtrip(self) -> None:
        source = "value?.name"
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        self.assertEqual(tokenize.untokenize(tokens), source)
        self.assertNotIn(tokenize.ERRORTOKEN, [token.type for token in tokens])

    def test_generator_call(self) -> None:
        self.assertIsNone(
            self.evaluate("value?.method(x for x in missing)", value=None)
        )
        self.assertEqual(
            self.evaluate(
                "value?.method(x for x in [1, 2])",
                value=SimpleNamespace(method=list),
            ),
            [1, 2],
        )

    def test_nested_chain_arguments(self) -> None:
        self.assertEqual(
            self.evaluate(
                "value?.method(other?.name).real",
                value=SimpleNamespace(method=lambda x: 5),
                other=None,
            ),
            5,
        )

    def test_grouped_assignment(self) -> None:
        profile = SimpleNamespace(name="Ada")
        namespace = {"value": SimpleNamespace(profile=profile)}
        exec("(value?.profile).name = 'Grace'", namespace)
        self.assertEqual(profile.name, "Grace")

    def test_annotation_unparse(self) -> None:
        namespace = {"value": None}
        exec("from __future__ import annotations\nx: value?.name", namespace)
        annotation = namespace["__annotations__"]["x"]
        self.assertIsNone(eval(annotation, namespace))

    def test_numeric_base(self) -> None:
        self.assertEqual(self.evaluate("1?.real"), 1)
        self.assertEqual(self.evaluate("1.5?.real"), 1.5)

    def test_ast_validation(self) -> None:
        tree = ast.parse("value?.name", mode="eval")
        tree.body.value.value.ctx = ast.Store()
        with self.assertRaises(ValueError):
            compile(tree, "<ast>", "eval")


if __name__ == "__main__":
    unittest.main()
