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

Both AST nodes support validation, preprocessing, symbol analysis and unparsing.
Unparsing includes parentheses around an optional chain to preserve its boundary.
Programs without the new syntax keep their original AST and compilation path.
External IDEs, formatters and type checkers do not yet understand this syntax.

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
