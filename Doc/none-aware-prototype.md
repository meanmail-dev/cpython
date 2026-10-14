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
emits `JUMP_IF_NONE` for every checked access. This opcode checks the top stack
reference by identity and preserves it on both branch outcomes. A `None` jumps
past the whole chain; otherwise the following `LOAD_ATTR` consumes the original
operand. There is no `COPY`, repeated lookup or temporary local. The same opcode
handles local variables at any slot, globals, calls and intermediate results.
Its stack effect is zero for both branch outcomes. Jump offsets and local loads
use the ordinary `EXTENDED_ARG` encoding when necessary.

For example, `return user?.profile?.name` compiles to:

```text
LOAD_FAST user
JUMP_IF_NONE end
NOT_TAKEN
LOAD_ATTR profile
JUMP_IF_NONE end
NOT_TAKEN
LOAD_ATTR name
end:
RETURN_VALUE
```

`JUMP_IF_NOT_NONE` is the stack-preserving inverse used when the CFG normalizes a
backward conditional jump. Both opcodes have one branch-history cache entry,
instrumented variants for `sys.monitoring`, and stack-preserving tier-two guards.
The CFG reference analysis keeps the operand owned across the branch boundary;
it does not borrow a local whose lifetime has not been proven across blocks.
Opcode tracing and monitoring callbacks may rebind locals without changing the
operand already on the stack.

The former fast-local/COPY fusion has been removed. Adding the opcodes changes
bytecode numbering, so this fork uses `.pyc` magic number 3667 instead of stock
3.15.0's 3666. Cached bytecode from the previous build is invalidated. Generated
opcode metadata, interpreter cases and the frozen-main test fixture must match
the new instruction set.

Both AST nodes support validation, preprocessing, symbol analysis and unparsing.
Unparsing includes parentheses around an optional chain to preserve its boundary.
Programs without the new syntax keep their original AST and compilation path.
External IDEs, formatters and type checkers do not yet understand this syntax.

## Local optimization measurements

Measured on Apple M3 Pro with a non-debug Clang `-O3` build, without PGO or LTO.
Each sample times one million function calls; each build runs 51 rounds with
shuffled cases and shuffled ordering of the optional and explicit variants.
Objects are `types.SimpleNamespace` instances. The explicit chain checks `user`
and then `profile` for `None`, saving `profile` in a temporary local. Timings
include the function call itself. The final new-opcode run was performed after
the full test suite and all builds finished.

| Case | Original COPY-based `?.`, ns | New opcode `?.`, ns | Explicit check, ns | Median paired ratio |
| --- | ---: | ---: | ---: | ---: |
| Attribute, `None` | 15.15 | 15.52 | 15.70 | 0.989 |
| Attribute, object | 33.72 | 32.71 | 35.89 | 0.915 |
| Chain, `None` | 15.33 | 15.50 | 16.36 | 0.944 |
| Chain, missing profile | 36.28 | 34.00 | 38.63 | 0.867 |
| Chain, full object | 51.04 | 48.96 | 51.95 | 0.935 |

The ratio is the median of the within-round optional/explicit time ratios. The
explicit column comes from the same new-opcode build. The original prototype
was timed separately; cross-build differences also include scheduling, clock
and interpreter layout changes. An earlier new-opcode run while the last GUI
test was still running gave 15.72/15.97 ns for `None` and 31.71/35.73 ns for
object access, showing the remaining variability. These local microbenchmarks
show no measured penalty against explicit checks in these five cases, but do
not prove zero overhead for all expressions, objects or workloads. In particular,
method-call optimization and whole-application performance were not measured.

Verification on macOS arm64:

- Full debug-build suite: `./python.exe -E -Wd -m test -j8 -u all --timeout=900
  --rerun --slowest`, exit 0, `FAILURE then SUCCESS`. All 558 test files were
  processed; 478 passed, 78 were skipped and two resource-denied. The runner
  counted 53,158 test cases, three initial failures and 2,607 skips. The initial
  frozen-main fixture failure was fixed by regenerating it for the new opcode
  numbering; two Tkinter failures passed their automatic targeted rerun. Duration
  14 minutes 16 seconds. This was not a clean first-pass result.
- A separate debug build with `--enable-experimental-jit=interpreter` passed
  `test_none_aware test_capi.test_opt test_monitoring test_sys_settrace -j2
  --timeout=600`: 890 test cases, four skips, all four files passed. The feature
  test confirms that executors contain the new stack-preserving guards and that
  mixed `None`/object chains return correct results after warmup.
- The non-debug release build passed all 38 feature tests, with the tier-two test
  skipped because that build disables tier two. Tests cover local slots 16 and
  256, extended jump offsets, both monitored branch outcomes, original-operand
  lifetime under opcode tracing, unbound locals and dynamic namespace lookups.
- Ruff formatting and `ruff check --fix --unsafe-fixes --preview` passed for the
  changed tests. Configured mypy checks passed for `Tools/cases_generator` (19
  source files) and `Tools/build` (12 source files).

Native JIT machine-code generation, free-threaded builds and Linux/Windows were
not validated. The earlier FinCtrl application tests apply to the original
prototype; they were not repeated after adding the opcode.

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

After changing opcodes, regenerate interpreter sources and the frozen-main
fixture with the newly built interpreter:

```sh
make regen-cases
make -j8
make regen-test-frozenmain
make -j8
./python.exe -m test test_none_aware test_embed test_importlib test_monitoring
```

To exercise tier-two translation without building native JIT stencils, configure
a separate checkout with `--with-pydebug --enable-experimental-jit=interpreter`
and run `test_none_aware`, `test_capi.test_opt`, `test_monitoring` and
`test_sys_settrace`. Native JIT code generation and free-threaded builds need
separate validation.

The ordinary CPython and Python Software Foundation licensing continues to apply.
