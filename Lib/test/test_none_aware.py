import ast
import io
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
