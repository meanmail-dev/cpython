# None-aware attribute access prototype

This experimental fork adds `?.` to CPython 3.15.0. The prototype branch is
`meanmail/none-aware-access-3.15`, based on upstream tag `v3.15.0` at
`d220409a13b77de7a6c30abe16ea90db01528edc`. The default `main` branch is reserved
for upstream synchronization.

```python
name = user?.profile?.name
name = user?.profile.name.upper() or "Unknown"
```

Each `?.` evaluates its left operand once. If it is `None`, the remaining
attribute accesses, calls and subscripts in that primary expression are skipped
and the result is `None`. Other falsy values are processed normally. No temporary
names are introduced and no exceptions from existing objects are suppressed.

- `user?.profile.name` returns `None` when `user` is `None`.
- If `user` exists but `user.profile` is `None`, `.name` raises `AttributeError`.
- `user?.profile?.name` also checks the profile for `None`.
- `user?.method(expensive())[index()]` skips both functions when `user` is `None`.
- `(user?.profile).name` ends the chain at the closing parenthesis and may raise.
- Direct assignments, augmented assignments and deletion through a chain are
  rejected. `(user?.profile).name = value` is an ordinary assignment to the
  independently evaluated result and retains ordinary Python behavior.

The lexer recognizes `?.` as one token; whitespace between `?` and `.` is not
supported. This is an intentionally small experiment inspired by
[PEP 823](https://peps.python.org/pep-0823/), not a full implementation of that
proposal. `?[...]`, `?()`, `??` and `??=` are not implemented.

## Implementation

`NoneAwareAttribute(value, attr)` represents each checked access.
`OptionalChain(value)` marks the whole primary expression containing checked
accesses. Wrapping occurs when a primary expression is completed, so parentheses
and independently evaluated arguments establish distinct chains. The compiler
uses existing `COPY` and `POP_JUMP_IF_NONE` instructions, leaving `None` on the
stack and jumping past the tail when a check fails. No new opcode is needed.

For an initialized fast local in slots 0 through 15, the CFG optimizer combines
`LOAD_FAST; COPY 1; POP_JUMP_IF_NONE` into
`LOAD_FAST_LOAD_FAST; POP_JUMP_IF_NONE`. This removes one instruction dispatch
without adding another lookup after the check. Both stack references refer to
the original operand, even if an opcode trace callback rebinds the local before
the check. The references retain their normal ownership. Uninitialized locals,
higher-numbered slots, dynamic name lookups and intermediate chain results keep
the original implementation. Fusion also respects source-line boundaries.

Both AST nodes support validation, preprocessing, symbol analysis and unparsing.
Unparsing includes parentheses around an optional chain to preserve its boundary.
Programs without the new syntax keep their original AST and compilation path.
External IDEs, formatters and type checkers do not yet understand this syntax.

## Local optimization measurements

Measured on Apple M3 Pro with a non-debug Clang `-O3` build, without PGO or LTO.
Each sample times one million function calls. Each build runs 51 rounds, with
shuffled cases and shuffled ordering of the optional and explicit variants.
Objects are `types.SimpleNamespace` instances. The explicit chain uses two
`is None` checks and stores `profile` in a temporary local. Values below are
median nanoseconds per call, including the function call itself.

| Case | Original `?.` | Fused `?.` | Explicit check in fused build |
| --- | ---: | ---: | ---: |
| Attribute, `None` | 16.01 | 16.05 | 15.22 |
| Attribute, object | 34.02 | 34.11 | 34.22 |
| Chain, `None` | 16.31 | 16.29 | 17.04 |
| Chain, missing profile | 37.69 | 36.25 | 37.60 |
| Chain, full object | 52.98 | 51.67 | 53.91 |

The original and fused builds were timed separately, so cross-build differences
also include scheduling and clock variation. Background load produced outliers;
these figures do not establish statistically significant speedups. Fusion
removes an instruction dispatch, but does not remove reference ownership costs
or the `COPY` used for intermediate results. Object access is approximately on
par with the explicit check here; the `None` case still shows a small difference.
Zero overhead across all cases is not established.

Optimization regression tests cover bytecode fusion, the local-slot limit,
unbound locals, dynamic namespace lookup, expression stack balance and rebinding
through opcode tracing. The earlier local-reload approach was discarded because
it failed the tracing regression. After fusion, the compiler, AST, grammar,
unparser, tokenizer, symbol-table, peephole, disassembly, tracing and monitoring
test files pass. The earlier full-suite result applies to the original prototype;
the complete suite has not been repeated for this optimization.

## Build and run

Install the ordinary CPython build dependencies first. On macOS with Homebrew:

```sh
brew install pkgconf openssl@3 readline sqlite xz zstd
./configure --with-pydebug --with-openssl="$(brew --prefix openssl@3)"
make -j8
./python.exe -c 'user = None; print(user?.profile.name)'
```

On Linux, use the normal CPython build prerequisites and `./python` instead of
`./python.exe`. This prototype is tested locally on macOS arm64.

After editing the grammar or AST, regenerate the derived source files in order:

```sh
make regen-token
make regen-ast
make regen-pegen
make -j8
./python.exe -m test test_none_aware test_grammar test_ast test_unparse test_tokenize test_compile test_symtable
```

The ordinary CPython and Python Software Foundation licensing continues to apply.
