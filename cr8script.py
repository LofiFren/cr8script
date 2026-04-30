#!/usr/bin/env python3
# cr8script: a simpler scripting language for LLMs and anyone who wants
# quick scripts without Python's footguns.
#
# Usage:
#   python3 cr8script.py path/to/program.cr8    Run a file
#   python3 cr8script.py                         Start the REPL
#   python3 cr8script.py --lex <file>            Dump tokens
#   python3 cr8script.py --ast <file>            Dump the AST
#   python3 cr8script.py --check <file>          Static checks only (friendly text)
#   python3 cr8script.py --check-json <file>     Static checks only (JSON for LLMs)
#   python3 cr8script.py --test                  Run golden tests in testdata/

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional


def _is_number(v) -> bool:
    """True if v is a cr8script number. Booleans are not numbers."""
    return isinstance(v, Decimal) and not isinstance(v, bool)


def _num(x) -> Decimal:
    """Coerce a Python int/str/Decimal to a cr8script number.

    Strings are parsed *as strings* so `0.1` stays exact rather than
    inheriting the float `0.1000000000000000055...` representation.
    """
    if isinstance(x, Decimal):
        return x
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, float):
        # Force through repr() to avoid the binary-float drift; repr(0.1) == "0.1".
        return Decimal(repr(x))
    if isinstance(x, str):
        return Decimal(x)
    raise TypeError(f"can't convert {type(x).__name__} to a cr8script number")


# ============================================================
#  Errors
# ============================================================

class PlainError(Exception):
    """A user-facing error. Always carries a line and ideally a hint."""

    def __init__(self, message: str, line: Optional[int] = None,
                 hint: Optional[str] = None):
        self.message = message
        self.line = line
        self.hint = hint
        super().__init__(self.format())

    def format(self) -> str:
        location = f" (line {self.line})" if self.line is not None else ""
        out = f"error{location}: {self.message}"
        if self.hint:
            out += f"\n  hint: {self.hint}"
        return out


# ============================================================
#  Lexer
# ============================================================

# Hard keywords: cannot be used as variable names anywhere.
KEYWORDS = {
    "let", "var", "if", "then", "else", "end", "for", "in",
    "repeat", "to", "return", "try", "otherwise",
    "true", "false", "nothing", "and", "or", "not", "mod",
    "is", "greater", "less", "than", "at", "least", "most",
    "show", "where", "sort", "take", "map", "group", "summarize",
}
# Soft keywords: act as keywords only in specific syntactic positions
# (e.g. `times` in `repeat N times`, `by` in `sort by`). Otherwise they are
# regular names and the user can bind them with `let times = ...`.
SOFT_KEYWORDS = {
    "each", "times", "by", "as", "descending", "ascending",
    "of", "from", "with",
}


@dataclass
class Token:
    kind: str
    value: Any
    line: int
    col: int

    def __repr__(self) -> str:
        return f"<{self.kind} {self.value!r} @{self.line}:{self.col}>"


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
    col = 1

    while i < len(src):
        c = src[i]

        if c == "\n":
            tokens.append(Token("NEWLINE", "\n", line, col))
            i += 1
            line += 1
            col = 1
            continue

        if c in " \t\r":
            i += 1
            col += 1
            continue

        if c == "#":
            while i < len(src) and src[i] != "\n":
                i += 1
            continue

        if c.isdigit():
            j = i
            saw_dot = False
            while j < len(src) and (
                src[j].isdigit()
                or (
                    src[j] == "."
                    and not saw_dot
                    and j + 1 < len(src)
                    and src[j + 1].isdigit()
                )
            ):
                if src[j] == ".":
                    saw_dot = True
                j += 1
            text = src[i:j]
            tokens.append(Token("NUMBER", Decimal(text), line, col))
            col += j - i
            i = j
            continue

        # f-string: f"hello {name}" -- emits a single FSTRING token whose value
        # is a list of ("text", str) and ("expr", (source, line)) parts.
        if c == "f" and i + 1 < len(src) and src[i + 1] == '"':
            start_line = line
            start_col = col
            j = i + 2
            parts: list = []
            text_buf: list[str] = []
            while True:
                if j >= len(src):
                    raise PlainError(
                        "unterminated f-string",
                        line=start_line,
                        hint='did you forget a closing "?',
                    )
                ch = src[j]
                if ch == '"':
                    if text_buf:
                        parts.append(("text", "".join(text_buf)))
                        text_buf = []
                    j += 1
                    break
                if ch == "\n":
                    raise PlainError(
                        "f-strings can't span multiple lines",
                        line=start_line,
                        hint='close the string with " before the line ends',
                    )
                if ch == "{":
                    if j + 1 < len(src) and src[j + 1] == "{":
                        text_buf.append("{")
                        j += 2
                        continue
                    if text_buf:
                        parts.append(("text", "".join(text_buf)))
                        text_buf = []
                    expr_start = j + 1
                    expr_line = line
                    depth = 1
                    k = expr_start
                    while k < len(src) and depth > 0:
                        if src[k] == "{":
                            depth += 1
                        elif src[k] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        elif src[k] == "\n":
                            raise PlainError(
                                "f-string expression must stay on one line",
                                line=expr_line,
                                hint="break complex expressions into a let-binding before the f-string",
                            )
                        elif src[k] == '"':
                            raise PlainError(
                                "nested strings inside f-string expressions aren't supported",
                                line=expr_line,
                                hint="bind the inner string to a name first, then reference the name",
                            )
                        k += 1
                    if depth != 0:
                        raise PlainError(
                            "unterminated `{` in f-string",
                            line=expr_line,
                            hint="every `{` needs a matching `}`",
                        )
                    expr_src = src[expr_start:k]
                    if not expr_src.strip():
                        raise PlainError(
                            "empty `{}` in f-string",
                            line=expr_line,
                            hint="put an expression between the braces, e.g. `{name}`",
                        )
                    parts.append(("expr", (expr_src, expr_line)))
                    j = k + 1  # past `}`
                    continue
                if ch == "}":
                    if j + 1 < len(src) and src[j + 1] == "}":
                        text_buf.append("}")
                        j += 2
                        continue
                    raise PlainError(
                        "unmatched `}` in f-string",
                        line=line,
                        hint="use `}}` for a literal `}`",
                    )
                if ch == "\\" and j + 1 < len(src):
                    nxt = src[j + 1]
                    text_buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
                    j += 2
                    continue
                text_buf.append(ch)
                j += 1
            tokens.append(Token("FSTRING", parts, start_line, start_col))
            length = j - i
            col += length
            i = j
            continue

        if c == '"':
            j = i + 1
            buf: list[str] = []
            start_line = line
            start_col = col
            while j < len(src) and src[j] != '"':
                if src[j] == "\n":
                    raise PlainError(
                        "strings can't span multiple lines",
                        line=start_line,
                        hint='close the string with " before the line ends',
                    )
                if src[j] == "\\" and j + 1 < len(src):
                    nxt = src[j + 1]
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
                    j += 2
                else:
                    buf.append(src[j])
                    j += 1
            if j >= len(src):
                raise PlainError(
                    "unterminated string",
                    line=start_line,
                    hint='did you forget a closing "?',
                )
            tokens.append(Token("STRING", "".join(buf), start_line, start_col))
            length = (j + 1) - i
            col += length
            i = j + 1
            continue

        if c.isalpha() or c == "_":
            j = i
            while j < len(src) and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            kind = "KEYWORD" if word in KEYWORDS else "NAME"
            tokens.append(Token(kind, word, line, col))
            col += j - i
            i = j
            continue

        if i + 1 < len(src) and src[i:i + 2] == "..":
            tokens.append(Token("OP", "..", line, col))
            i += 2
            col += 2
            continue

        if c in "+-*/(){}[],:|=.":
            tokens.append(Token("OP", c, line, col))
            i += 1
            col += 1
            continue

        raise PlainError(f"unexpected character {c!r}", line=line)

    tokens.append(Token("EOF", None, line, col))
    return tokens


# ============================================================
#  AST nodes
# ============================================================

# Expressions
@dataclass
class Num:        value: float;             line: int = 0
@dataclass
class Str:        value: str;               line: int = 0
@dataclass
class FString:   parts: list;              line: int = 0  # list of (str | expression-AST)
@dataclass
class Bool:       value: bool;              line: int = 0
@dataclass
class Nothing:    line: int = 0
@dataclass
class Name:       ident: str;               line: int = 0
@dataclass
class List_:     items: list;               line: int = 0
@dataclass
class Record:    fields: list;              line: int = 0  # list of (name, expr)
@dataclass
class Range_:    start: Any;  end: Any;     line: int = 0
@dataclass
class BinOp:     op: str;  left: Any;  right: Any;          line: int = 0
@dataclass
class UnaryOp:   op: str;  operand: Any;                    line: int = 0
@dataclass
class Call:      func: Any;  args: list;                    line: int = 0
@dataclass
class FieldAccess: obj: Any;  field: str;                   line: int = 0
@dataclass
class Index:     obj: Any;  key: Any;                       line: int = 0
@dataclass
class Pipeline:  source: Any;  stages: list;                line: int = 0
@dataclass
class IfExpr:
    cond: Any
    then_val: Any
    elifs: list      # list of (cond_expr, value_expr)
    else_val: Any    # expression (required for if-expr; defaults to nothing if missing)
    line: int = 0
@dataclass
class PipelineStage:
    verb: str
    arg: Any                  # an expression or None
    field_scoped: bool        # if True, evaluate per-item with item fields bound
    descending: bool = False  # for sort by
    group_key_name: Optional[str] = None  # for group by <name>
    line: int = 0

# Statements
@dataclass
class Let:       name: str;  value: Any;  mutable: bool;    line: int = 0
@dataclass
class Assign:    name: str;  value: Any;                    line: int = 0
@dataclass
class If:
    cond: Any
    then: list
    elifs: list      # list of (cond, body)
    else_: list      # list of stmts (may be empty)
    line: int = 0
@dataclass
class For:       name: str;  iter: Any;  body: list;        line: int = 0
@dataclass
class Repeat:    count: Any;  body: list;                   line: int = 0
@dataclass
class FuncDef:   name: str;  params: list;  body: list;     line: int = 0
@dataclass
class Return_:   value: Any;                                line: int = 0
@dataclass
class Try_:      body: list;  err_name: str;  otherwise: list; line: int = 0
@dataclass
class Show:      expr: Any;                                  line: int = 0
@dataclass
class ExprStmt:  expr: Any;                                  line: int = 0


# ============================================================
#  Parser
# ============================================================

class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # --- token helpers ---
    def peek(self, offset: int = 0) -> Token:
        idx = self.pos + offset
        if idx >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[idx]

    def at_end(self) -> bool:
        return self.peek().kind == "EOF"

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def check(self, kind: str, value: Any = None) -> bool:
        t = self.peek()
        if t.kind != kind:
            return False
        return value is None or t.value == value

    def check_kw(self, *words: str) -> bool:
        t = self.peek()
        return t.kind == "KEYWORD" and t.value in words

    def check_op(self, *ops: str) -> bool:
        t = self.peek()
        return t.kind == "OP" and t.value in ops

    def match_op(self, *ops: str) -> Optional[Token]:
        if self.check_op(*ops):
            return self.advance()
        return None

    def match_kw(self, *words: str) -> Optional[Token]:
        if self.check_kw(*words):
            return self.advance()
        return None

    # Soft keywords: lexed as NAME, but recognised contextually here.
    def check_soft(self, *words: str) -> bool:
        t = self.peek()
        return t.kind == "NAME" and t.value in words

    def match_soft(self, *words: str) -> Optional[Token]:
        if self.check_soft(*words):
            return self.advance()
        return None

    def expect_soft(self, word: str, hint: str = "") -> Token:
        if not self.check_soft(word):
            t = self.peek()
            raise PlainError(
                f"expected `{word}`, got `{self._friendly(t)}`",
                line=t.line,
                hint=hint or None,
            )
        return self.advance()

    def expect_kw(self, word: str, hint: str = "") -> Token:
        if not self.check_kw(word):
            t = self.peek()
            raise PlainError(
                f"expected `{word}`, got `{self._friendly(t)}`",
                line=t.line,
                hint=hint or None,
            )
        return self.advance()

    def expect_op(self, op: str, hint: str = "") -> Token:
        if not self.check_op(op):
            t = self.peek()
            raise PlainError(
                f"expected `{op}`, got `{self._friendly(t)}`",
                line=t.line,
                hint=hint or None,
            )
        return self.advance()

    def expect_name(self, hint: str = "") -> Token:
        if self.peek().kind != "NAME":
            t = self.peek()
            raise PlainError(
                f"expected a name, got `{self._friendly(t)}`",
                line=t.line,
                hint=hint or None,
            )
        return self.advance()

    @staticmethod
    def _friendly(t: Token) -> str:
        if t.kind == "EOF":
            return "end of file"
        if t.kind == "NEWLINE":
            return "end of line"
        return str(t.value)

    def skip_newlines(self):
        while self.check("NEWLINE"):
            self.advance()

    # --- top-level ---
    def parse_program(self) -> list:
        stmts = []
        self.skip_newlines()
        while not self.at_end():
            stmts.append(self.parse_statement())
            self.skip_newlines()
        return stmts

    def parse_block_until(self, *terminator_kws: str) -> list:
        """Parse statements until a terminator keyword; do not consume it."""
        stmts = []
        self.skip_newlines()
        while not self.at_end() and not self.check_kw(*terminator_kws):
            stmts.append(self.parse_statement())
            self.skip_newlines()
        if self.at_end():
            raise PlainError(
                f"missing `{terminator_kws[-1]}` to close this block",
                line=self.peek().line,
                hint=f"every block opened with `if`/`for`/`to`/`try`/`repeat` needs a matching `end`",
            )
        return stmts

    # --- statements ---
    def parse_statement(self):
        t = self.peek()
        if t.kind == "KEYWORD":
            if t.value == "let":
                return self.parse_let(mutable=False)
            if t.value == "var":
                return self.parse_let(mutable=True)
            if t.value == "if":
                return self.parse_if()
            if t.value == "for":
                return self.parse_for()
            if t.value == "repeat":
                return self.parse_repeat()
            if t.value == "to":
                return self.parse_func_def()
            if t.value == "return":
                return self.parse_return()
            if t.value == "try":
                return self.parse_try()
            if t.value == "show":
                self.advance()
                expr = self.parse_expr()
                return Show(expr, line=t.line)

        # assignment: NAME = expr
        if t.kind == "NAME" and self.peek(1).kind == "OP" and self.peek(1).value == "=":
            name = self.advance().value
            self.advance()  # =
            value = self.parse_expr()
            return Assign(name, value, line=t.line)

        expr = self.parse_expr()
        return ExprStmt(expr, line=t.line)

    def parse_let(self, mutable: bool):
        kw = self.advance()  # let | var
        name_tok = self.expect_name(
            hint=f"`{kw.value}` should be followed by a name, like `{kw.value} count = 0`"
        )
        self.expect_op(
            "=",
            hint=f"`{kw.value} {name_tok.value}` should be followed by `=`, like `{kw.value} {name_tok.value} = 1`",
        )
        value = self.parse_expr()
        return Let(name_tok.value, value, mutable, line=kw.line)

    def parse_if(self):
        if_tok = self.advance()  # if
        cond = self.parse_expr()
        self.expect_kw("then", hint="`if <cond> then ... end` -- did you forget `then`?")
        body = self.parse_block_until("else", "end")
        elifs = []
        else_ = []
        while self.match_kw("else"):
            if self.match_kw("if"):
                ec = self.parse_expr()
                self.expect_kw("then")
                eb = self.parse_block_until("else", "end")
                elifs.append((ec, eb))
            else:
                else_ = self.parse_block_until("end")
                break
        self.expect_kw("end")
        return If(cond, body, elifs, else_, line=if_tok.line)

    def parse_for(self):
        for_tok = self.advance()  # for
        self.expect_soft(
            "each",
            hint="loops are written `for each <name> in <thing> ... end`",
        )
        name_tok = self.expect_name()
        self.expect_kw("in")
        iter_expr = self.parse_expr()
        body = self.parse_block_until("end")
        self.expect_kw("end")
        return For(name_tok.value, iter_expr, body, line=for_tok.line)

    def parse_repeat(self):
        rep_tok = self.advance()  # repeat
        count = self.parse_expr()
        self.expect_soft(
            "times",
            hint="`repeat` is written `repeat <n> times ... end`",
        )
        body = self.parse_block_until("end")
        self.expect_kw("end")
        return Repeat(count, body, line=rep_tok.line)

    def parse_func_def(self):
        to_tok = self.advance()  # to
        name_tok = self.expect_name(
            hint="functions are written `to <name>(args) ... end`"
        )
        self.expect_op("(", hint="function name should be followed by `(`")
        params: list[str] = []
        if not self.check_op(")"):
            params.append(self.expect_name().value)
            while self.match_op(","):
                params.append(self.expect_name().value)
        self.expect_op(")")
        body = self.parse_block_until("end")
        self.expect_kw("end")
        return FuncDef(name_tok.value, params, body, line=to_tok.line)

    def parse_return(self):
        ret_tok = self.advance()
        # `return` may stand alone or be followed by an expression on the same line
        if self.check("NEWLINE") or self.check_kw("end"):
            return Return_(Nothing(line=ret_tok.line), line=ret_tok.line)
        return Return_(self.parse_expr(), line=ret_tok.line)

    def parse_try(self):
        try_tok = self.advance()  # try
        body = self.parse_block_until("otherwise")
        self.expect_kw("otherwise")
        self.expect_soft("as", hint="errors are caught with `try ... otherwise as <name> ... end`")
        err_name = self.expect_name().value
        otherwise = self.parse_block_until("end")
        self.expect_kw("end")
        return Try_(body, err_name, otherwise, line=try_tok.line)

    # --- expressions ---
    # Precedence (low -> high):
    #   pipeline:    expr | verb ...
    #   or
    #   and
    #   not
    #   compare:     is, is not, is greater than, is at least, is less than, is at most
    #   add:         + -
    #   mul:         * / mod
    #   unary:       - +
    #   power:       (none in v1)
    #   postfix:     . [] (call)
    #   primary

    def parse_expr(self):
        return self.parse_pipeline()

    def parse_pipeline(self):
        left = self.parse_or()
        if not self._peek_pipe():
            return left
        stages: list[PipelineStage] = []
        first_line = self.peek().line
        while self._peek_pipe():
            self.skip_newlines()
            self.advance()  # |
            self.skip_newlines()
            stage = self.parse_pipeline_stage()
            stages.append(stage)
        return Pipeline(left, stages, line=first_line)

    def _peek_pipe(self) -> bool:
        """True if the next non-newline token is `|`. Pipelines may span lines."""
        i = self.pos
        while i < len(self.tokens) and self.tokens[i].kind == "NEWLINE":
            i += 1
        if i >= len(self.tokens):
            return False
        t = self.tokens[i]
        return t.kind == "OP" and t.value == "|"

    def parse_pipeline_stage(self) -> PipelineStage:
        t = self.peek()
        verbs = ("where", "sort", "take", "map", "group", "summarize")
        if not (t.kind == "KEYWORD" and t.value in verbs):
            raise PlainError(
                f"after `|` I expected a verb (where, sort by, take, map, group by, summarize), "
                f"got `{self._friendly(t)}`",
                line=t.line,
                hint="pipelines look like `things | where age is at least 18 | take 5`",
            )
        verb_tok = self.advance()
        if verb_tok.value == "where":
            expr = self.parse_or()
            return PipelineStage("where", expr, field_scoped=True, line=verb_tok.line)
        if verb_tok.value == "sort":
            self.expect_soft("by", hint="write `sort by <name>` (e.g. `sort by age`)")
            expr = self.parse_or()
            descending = False
            if self.match_soft("descending"):
                descending = True
            elif self.match_soft("ascending"):
                descending = False
            return PipelineStage("sort", expr, field_scoped=True,
                                 descending=descending, line=verb_tok.line)
        if verb_tok.value == "take":
            n = self.parse_or()
            return PipelineStage("take", n, field_scoped=False, line=verb_tok.line)
        if verb_tok.value == "map":
            expr = self.parse_or()
            return PipelineStage("map", expr, field_scoped=True, line=verb_tok.line)
        if verb_tok.value == "group":
            self.expect_soft("by", hint="write `group by <field>` (e.g. `group by category`)")
            expr = self.parse_or()
            # When the group expression is a bare name, use it as the field
            # name in the result records: `group by category` ->
            # `{category: ..., items: [...]}`. Otherwise fall back to `key`.
            group_key_name = expr.ident if isinstance(expr, Name) else None
            return PipelineStage("group", expr, field_scoped=True,
                                 group_key_name=group_key_name, line=verb_tok.line)
        if verb_tok.value == "summarize":
            expr = self.parse_or()
            if not isinstance(expr, Record):
                raise PlainError(
                    "`summarize` needs a record literal",
                    line=verb_tok.line,
                    hint='write `| summarize { total: sum(amount), n: length(items) }`',
                )
            return PipelineStage("summarize", expr, field_scoped=False, line=verb_tok.line)
        raise PlainError(f"unknown verb `{verb_tok.value}`", line=verb_tok.line)

    def parse_or(self):
        left = self.parse_and()
        while self.match_kw("or"):
            right = self.parse_and()
            left = BinOp("or", left, right, line=left.line)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.match_kw("and"):
            right = self.parse_not()
            left = BinOp("and", left, right, line=left.line)
        return left

    def parse_not(self):
        if self.check_kw("not"):
            t = self.advance()
            operand = self.parse_compare()
            return UnaryOp("not", operand, line=t.line)
        return self.parse_compare()

    def parse_compare(self):
        left = self.parse_add()
        # consume one comparison; comparisons don't chain (a is b is c -> not allowed)
        if self.check_kw("is"):
            is_tok = self.advance()
            # is not
            if self.match_kw("not"):
                # `is not nothing` -> ne to nothing
                if self.match_kw("nothing"):
                    return BinOp("ne", left, Nothing(line=is_tok.line), line=is_tok.line)
                right = self.parse_add()
                return BinOp("ne", left, right, line=is_tok.line)
            # is greater than
            if self.match_kw("greater"):
                self.expect_kw("than", hint="comparators read like `is greater than 10`")
                right = self.parse_add()
                return BinOp("gt", left, right, line=is_tok.line)
            # is less than
            if self.match_kw("less"):
                self.expect_kw("than", hint="comparators read like `is less than 10`")
                right = self.parse_add()
                return BinOp("lt", left, right, line=is_tok.line)
            # is at least
            # is at most
            if self.match_kw("at"):
                if self.match_kw("least"):
                    right = self.parse_add()
                    return BinOp("ge", left, right, line=is_tok.line)
                if self.match_kw("most"):
                    right = self.parse_add()
                    return BinOp("le", left, right, line=is_tok.line)
                t = self.peek()
                raise PlainError(
                    f"expected `least` or `most` after `is at`, got `{self._friendly(t)}`",
                    line=t.line,
                    hint="write `is at least 10` or `is at most 10`",
                )
            # is nothing
            if self.match_kw("nothing"):
                return BinOp("eq", left, Nothing(line=is_tok.line), line=is_tok.line)
            # plain `is` -> equality
            right = self.parse_add()
            return BinOp("eq", left, right, line=is_tok.line)
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.check_op("+", "-"):
            op_tok = self.advance()
            right = self.parse_mul()
            left = BinOp({"+": "add", "-": "sub"}[op_tok.value], left, right, line=op_tok.line)
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while True:
            if self.check_op("*", "/"):
                op_tok = self.advance()
                right = self.parse_unary()
                left = BinOp({"*": "mul", "/": "div"}[op_tok.value], left, right, line=op_tok.line)
            elif self.check_kw("mod"):
                op_tok = self.advance()
                right = self.parse_unary()
                left = BinOp("mod", left, right, line=op_tok.line)
            else:
                break
        return left

    def parse_unary(self):
        if self.check_op("-"):
            t = self.advance()
            return UnaryOp("neg", self.parse_postfix(), line=t.line)
        if self.check_op("+"):
            self.advance()
            return self.parse_postfix()
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while True:
            if self.check_op("."):
                dot = self.advance()
                # Any identifier-like token works as a field/method name,
                # even if it is a keyword (e.g. `.with`, `.from`).
                t = self.peek()
                if t.kind not in ("NAME", "KEYWORD"):
                    raise PlainError(
                        f"expected a field name after `.`, got `{self._friendly(t)}`",
                        line=dot.line,
                    )
                field_tok = self.advance()
                # Check for method call: x.foo(args)
                if self.check_op("("):
                    self.advance()
                    args = self.parse_call_args()
                    expr = Call(FieldAccess(expr, field_tok.value, line=dot.line),
                                args, line=dot.line)
                else:
                    expr = FieldAccess(expr, field_tok.value, line=dot.line)
                continue
            if self.check_op("["):
                lb = self.advance()
                key = self.parse_expr()
                self.expect_op("]")
                expr = Index(expr, key, line=lb.line)
                continue
            if self.check_op("(") and self._is_callable_target(expr):
                self.advance()
                args = self.parse_call_args()
                expr = Call(expr, args, line=expr.line)
                continue
            # Range: a..b (treated as postfix on `a`)
            if self.check_op(".."):
                t = self.advance()
                end = self.parse_unary()
                expr = Range_(expr, end, line=t.line)
                continue
            break
        return expr

    def _is_callable_target(self, node) -> bool:
        # Names and field-accesses can be callable. Don't accidentally treat
        # `(1+2)` followed by `(3)` as a call -- that's ambiguous, ban it.
        return isinstance(node, (Name, FieldAccess))

    def parse_call_args(self) -> list:
        # We've already consumed `(`
        args = []
        self.skip_newlines()
        if not self.check_op(")"):
            args.append(self.parse_expr())
            self.skip_newlines()
            while self.match_op(","):
                self.skip_newlines()
                args.append(self.parse_expr())
                self.skip_newlines()
        self.expect_op(")")
        return args

    def parse_primary(self):
        t = self.peek()
        if t.kind == "NUMBER":
            self.advance()
            return Num(t.value, line=t.line)
        if t.kind == "STRING":
            self.advance()
            return Str(t.value, line=t.line)
        if t.kind == "FSTRING":
            self.advance()
            parts: list = []
            for part in t.value:
                kind, content = part
                if kind == "text":
                    parts.append(content)
                    continue
                expr_src, expr_line = content
                sub_tokens = tokenize(expr_src + "\n")
                # Re-anchor the sub-tokens to the original line of the expression.
                for st in sub_tokens:
                    st.line = expr_line + (st.line - 1)
                sub_parser = Parser(sub_tokens)
                expr = sub_parser.parse_expr()
                # Skip trailing newlines/EOF; anything else means stray content.
                while sub_parser.peek().kind == "NEWLINE":
                    sub_parser.advance()
                if not sub_parser.at_end():
                    raise PlainError(
                        f"extra content after `{expr_src.strip()}` in f-string",
                        line=expr_line,
                        hint="each `{...}` holds exactly one expression",
                    )
                parts.append(expr)
            return FString(parts, line=t.line)
        if t.kind == "KEYWORD":
            if t.value == "true":
                self.advance()
                return Bool(True, line=t.line)
            if t.value == "false":
                self.advance()
                return Bool(False, line=t.line)
            if t.value == "nothing":
                self.advance()
                return Nothing(line=t.line)
            if t.value == "if":
                return self.parse_if_expr()
        if t.kind == "NAME":
            self.advance()
            return Name(t.value, line=t.line)
        if self.check_op("("):
            self.advance()
            self.skip_newlines()
            expr = self.parse_expr()
            self.skip_newlines()
            self.expect_op(")")
            return expr
        if self.check_op("["):
            lb = self.advance()
            items = []
            self.skip_newlines()
            if not self.check_op("]"):
                items.append(self.parse_expr())
                self.skip_newlines()
                while self.match_op(","):
                    self.skip_newlines()
                    if self.check_op("]"):
                        break
                    items.append(self.parse_expr())
                    self.skip_newlines()
            self.expect_op("]")
            return List_(items, line=lb.line)
        if self.check_op("{"):
            lb = self.advance()
            fields: list = []
            self.skip_newlines()
            if not self.check_op("}"):
                fields.append(self._parse_record_field())
                self.skip_newlines()
                while self.match_op(","):
                    self.skip_newlines()
                    if self.check_op("}"):
                        break
                    fields.append(self._parse_record_field())
                    self.skip_newlines()
            self.expect_op("}")
            return Record(fields, line=lb.line)
        raise PlainError(
            f"I don't know how to read `{self._friendly(t)}` here",
            line=t.line,
            hint="expressions are numbers, strings, names, lists [..], records {..}, or (parenthesized)",
        )

    def parse_if_expr(self):
        if_tok = self.advance()  # if
        cond = self.parse_or()
        self.expect_kw(
            "then",
            hint="`if <cond> then <value> else <value> end` produces a value",
        )
        self.skip_newlines()
        then_val = self.parse_or()
        elifs: list = []
        else_val = Nothing(line=if_tok.line)
        self.skip_newlines()
        while self.match_kw("else"):
            if self.match_kw("if"):
                ec = self.parse_or()
                self.expect_kw("then")
                self.skip_newlines()
                ev = self.parse_or()
                elifs.append((ec, ev))
                self.skip_newlines()
            else:
                self.skip_newlines()
                else_val = self.parse_or()
                self.skip_newlines()
                break
        self.expect_kw(
            "end",
            hint="if-expressions need to close with `end`",
        )
        return IfExpr(cond, then_val, elifs, else_val, line=if_tok.line)

    def _parse_record_field(self):
        # field key: bareword name OR string literal
        t = self.peek()
        if t.kind == "NAME":
            key = self.advance().value
        elif t.kind == "STRING":
            key = self.advance().value
        else:
            raise PlainError(
                f"record fields need a name before `:`, got `{self._friendly(t)}`",
                line=t.line,
                hint='record literals look like `{ name: "Ada", age: 36 }`',
            )
        self.expect_op(
            ":",
            hint=f"record field `{key}` should be followed by `:` and a value",
        )
        value = self.parse_expr()
        return (key, value)


# ============================================================
#  Runtime values
# ============================================================

class _NothingType:
    _inst = None
    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst
    def __repr__(self):
        return "nothing"
    def __bool__(self):
        return False
    def __eq__(self, other):
        return isinstance(other, _NothingType)
    def __hash__(self):
        return hash("__plain_nothing__")


NOTHING = _NothingType()


@dataclass
class PlainRecord:
    fields: dict  # str -> value

    def __repr__(self):
        return "{" + ", ".join(f"{k}: {format_value(v)}" for k, v in self.fields.items()) + "}"

    def get(self, key: str):
        return self.fields.get(key, NOTHING)

    def with_(self, **updates) -> "PlainRecord":
        new = dict(self.fields)
        new.update(updates)
        return PlainRecord(new)


class _GroupRecord(PlainRecord):
    """Marker subclass produced by `| group by ...` so a following `summarize`
    knows to aggregate per-group instead of over the whole list. Renders the
    same as PlainRecord -- invisible to user code unless they introspect."""


class PlainFunc:
    def __init__(self, name: str, params: list, body: list, closure: "Env"):
        self.name = name
        self.params = params
        self.body = body
        self.closure = closure
        self.is_builtin = False

    def __repr__(self):
        return f"<function {self.name}({', '.join(self.params)})>"


class BuiltinFunc:
    def __init__(self, name: str, fn: Callable, arity: Optional[tuple] = None):
        self.name = name
        self.fn = fn         # fn(args: list, line: int) -> value
        self.arity = arity   # (min, max) or None
        self.is_builtin = True

    def __repr__(self):
        return f"<builtin {self.name}>"


class PlainModule:
    """A namespace, e.g. `math` providing `math.sqrt`."""
    def __init__(self, name: str, members: dict):
        self.name = name
        self.members = members

    def __repr__(self):
        return f"<module {self.name}>"


# ============================================================
#  Environment
# ============================================================

class Env:
    def __init__(self, parent: Optional["Env"] = None):
        self.parent = parent
        self.vars: dict[str, list] = {}  # name -> [value, mutable]

    def define(self, name: str, value: Any, mutable: bool, line: Optional[int] = None):
        if name in self.vars:
            raise PlainError(
                f"`{name}` is already defined in this scope",
                line=line,
                hint="rename the new binding, or remove the old one",
            )
        self.vars[name] = [value, mutable]

    def get(self, name: str, line: Optional[int] = None):
        env: Optional[Env] = self
        while env is not None:
            if name in env.vars:
                return env.vars[name][0]
            env = env.parent
        raise PlainError(
            f"`{name}` is not defined",
            line=line,
            hint=f"did you mean to write `let {name} = ...` first?",
        )

    def assign(self, name: str, value: Any, line: Optional[int] = None):
        env: Optional[Env] = self
        while env is not None:
            if name in env.vars:
                if not env.vars[name][1]:
                    raise PlainError(
                        f"`{name}` is let-bound and cannot be changed",
                        line=line,
                        hint=f"declare it as `var {name} = ...` if it should change",
                    )
                env.vars[name][0] = value
                return
            env = env.parent
        raise PlainError(
            f"`{name}` is not defined; can't assign to it",
            line=line,
            hint=f"start it with `let {name} = ...` or `var {name} = ...`",
        )


# ============================================================
#  Evaluator
# ============================================================

class _Return(Exception):
    def __init__(self, value):
        self.value = value


def type_name(v) -> str:
    if v is NOTHING:
        return "nothing"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, Decimal):
        return "number"
    if isinstance(v, str):
        return "text"
    if isinstance(v, list):
        return "list"
    if isinstance(v, PlainRecord):
        return "record"
    if isinstance(v, (PlainFunc, BuiltinFunc)):
        return "function"
    if isinstance(v, PlainModule):
        return "module"
    return type(v).__name__


def format_value(v) -> str:
    if v is NOTHING:
        return "nothing"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        if v.is_nan():
            return "NaN"
        if v.is_infinite():
            return "infinity" if v > 0 else "-infinity"
        # Whole-number Decimals print without a fractional tail.
        if v == v.to_integral_value() and abs(v) < Decimal(10) ** 16:
            return str(int(v))
        # Avoid scientific notation; trim trailing zeros after the point.
        s = format(v, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "[" + ", ".join(format_value(x) for x in v) + "]"
    if isinstance(v, PlainRecord):
        return repr(v)
    return repr(v)


def _check_bool(v, line, where):
    if not isinstance(v, bool):
        raise PlainError(
            f"{where} must be true or false, got {type_name(v)} ({format_value(v)})",
            line=line,
            hint="cr8script has no truthy/falsy values -- use a real comparison like `x is greater than 0`",
        )
    return v


def _check_number(v, line, where):
    if not _is_number(v):
        raise PlainError(
            f"{where} needs a number, got {type_name(v)} ({format_value(v)})",
            line=line,
        )
    return v


def evaluate_program(stmts: list, env: Env):
    for s in stmts:
        evaluate(s, env)


def evaluate(node, env: Env):
    method = _DISPATCH.get(type(node))
    if method is None:
        raise PlainError(f"internal: no evaluator for {type(node).__name__}",
                         line=getattr(node, "line", None))
    return method(node, env)


# ----- expressions -----

def _eval_num(n: Num, env: Env):     return n.value
def _eval_str(n: Str, env: Env):     return n.value
def _eval_fstring(n: FString, env: Env):
    out: list[str] = []
    for p in n.parts:
        if isinstance(p, str):
            out.append(p)
        else:
            out.append(format_value(evaluate(p, env)))
    return "".join(out)
def _eval_bool(n: Bool, env: Env):   return n.value
def _eval_nothing(n: Nothing, env): return NOTHING

def _eval_name(n: Name, env: Env):
    return env.get(n.ident, line=n.line)

def _eval_list(n: List_, env: Env):
    return [evaluate(x, env) for x in n.items]

def _eval_record(n: Record, env: Env):
    out = {}
    for key, expr in n.fields:
        if key in out:
            raise PlainError(
                f"record has duplicate field `{key}`",
                line=n.line,
            )
        out[key] = evaluate(expr, env)
    return PlainRecord(out)

def _eval_range(n: Range_, env: Env):
    start = _check_number(evaluate(n.start, env), n.line, "range start")
    end = _check_number(evaluate(n.end, env), n.line, "range end")
    if start != start.to_integral_value() or end != end.to_integral_value():
        raise PlainError(
            "ranges need whole numbers (no decimals)",
            line=n.line,
            hint=f"got {format_value(start)}..{format_value(end)}",
        )
    s, e = int(start), int(end)
    if s <= e:
        return [Decimal(x) for x in range(s, e + 1)]
    return [Decimal(x) for x in range(s, e - 1, -1)]

def _eval_unary(n: UnaryOp, env: Env):
    v = evaluate(n.operand, env)
    if n.op == "neg":
        _check_number(v, n.line, "negation")
        return -v
    if n.op == "not":
        _check_bool(v, n.line, "`not` operand")
        return not v
    raise PlainError(f"unknown unary op {n.op}", line=n.line)

def _eval_binop(n: BinOp, env: Env):
    op = n.op
    # Short-circuit logical
    if op == "and":
        left = evaluate(n.left, env)
        _check_bool(left, n.line, "`and` left side")
        if not left:
            return False
        right = evaluate(n.right, env)
        _check_bool(right, n.line, "`and` right side")
        return right
    if op == "or":
        left = evaluate(n.left, env)
        _check_bool(left, n.line, "`or` left side")
        if left:
            return True
        right = evaluate(n.right, env)
        _check_bool(right, n.line, "`or` right side")
        return right

    left = evaluate(n.left, env)
    right = evaluate(n.right, env)

    if op in ("eq", "ne"):
        # Allow comparing nothing with anything; for other types require same type
        if left is NOTHING or right is NOTHING:
            same = (left is NOTHING) and (right is NOTHING)
            return same if op == "eq" else not same
        if type_name(left) != type_name(right):
            raise PlainError(
                f"can't compare {type_name(left)} ({format_value(left)}) with "
                f"{type_name(right)} ({format_value(right)})",
                line=n.line,
                hint="convert one of them first, e.g. `to_number(\"5\")`",
            )
        eq = left == right
        return eq if op == "eq" else not eq

    if op in ("lt", "le", "gt", "ge"):
        # Numbers and strings can be ordered
        if isinstance(left, str) and isinstance(right, str):
            cmp = {"lt": left < right, "le": left <= right,
                   "gt": left > right, "ge": left >= right}[op]
            return cmp
        _check_number(left, n.line, "left side of comparison")
        _check_number(right, n.line, "right side of comparison")
        cmp = {"lt": left < right, "le": left <= right,
               "gt": left > right, "ge": left >= right}[op]
        return cmp

    if op == "add":
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        if isinstance(left, list) and isinstance(right, list):
            return left + right
        if (isinstance(left, str) and not isinstance(right, str)) or \
           (isinstance(right, str) and not isinstance(left, str)):
            raise PlainError(
                f"can't add {type_name(left)} and {type_name(right)} -- "
                f"`+` won't silently mix types",
                line=n.line,
                hint=f"convert it explicitly: e.g. `to_text({format_value(right) if isinstance(left, str) else format_value(left)})`",
            )
        _check_number(left, n.line, "left side of `+`")
        _check_number(right, n.line, "right side of `+`")
        return left + right

    if op in ("sub", "mul", "div", "mod"):
        _check_number(left, n.line, f"left side of `{_op_glyph(op)}`")
        _check_number(right, n.line, f"right side of `{_op_glyph(op)}`")
        if op == "sub": return left - right
        if op == "mul": return left * right
        if op == "div":
            if right == 0:
                raise PlainError("can't divide by zero", line=n.line)
            return left / right
        if op == "mod":
            if right == 0:
                raise PlainError("can't take mod by zero", line=n.line)
            return left % right

    raise PlainError(f"unknown operator {op}", line=n.line)


def _op_glyph(op):
    return {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "mod"}.get(op, op)


def _eval_call(n: Call, env: Env):
    callee = evaluate(n.func, env)
    args = [evaluate(a, env) for a in n.args]
    return call_value(callee, args, n.line)


def call_value(callee, args, line):
    if isinstance(callee, BuiltinFunc):
        if callee.arity is not None:
            mn, mx = callee.arity
            if not (mn <= len(args) <= mx):
                want = f"{mn}" if mn == mx else f"{mn}-{mx}"
                raise PlainError(
                    f"`{callee.name}` takes {want} argument(s), got {len(args)}",
                    line=line,
                )
        return callee.fn(args, line)
    if isinstance(callee, PlainFunc):
        if len(args) != len(callee.params):
            raise PlainError(
                f"`{callee.name}` takes {len(callee.params)} argument(s), got {len(args)}",
                line=line,
                hint=f"expected: {', '.join(callee.params) or '(none)'}",
            )
        new_env = Env(callee.closure)
        for p, v in zip(callee.params, args):
            new_env.define(p, v, mutable=False)
        try:
            for s in callee.body:
                evaluate(s, new_env)
            return NOTHING
        except _Return as r:
            return r.value
    raise PlainError(
        f"can't call {type_name(callee)} ({format_value(callee)}) like a function",
        line=line,
        hint="only functions and built-ins can be called with `(...)`",
    )


def _eval_field(n: FieldAccess, env: Env):
    obj = evaluate(n.obj, env)
    return _field_of(obj, n.field, n.line)


def _field_of(obj, field, line):
    # Modules expose members
    if isinstance(obj, PlainModule):
        if field in obj.members:
            return obj.members[field]
        raise PlainError(
            f"module `{obj.name}` has no member `{field}`",
            line=line,
            hint=f"available: {', '.join(sorted(obj.members)) or '(none)'}",
        )
    # Records: properties first, then methods, then user fields. Missing is an error.
    if isinstance(obj, PlainRecord):
        if field in _RECORD_PROPS:
            return _RECORD_PROPS[field](obj, line)
        if field in _RECORD_METHODS:
            return _bind_method(_RECORD_METHODS[field], obj)
        if field in obj.fields:
            return obj.fields[field]
        raise PlainError(
            f"record has no field `{field}`",
            line=line,
            hint=f"available: {', '.join(obj.fields) or '(none)'}; "
                 f"use `.get(\"{field}\")` to get `nothing` instead of an error",
        )
    if isinstance(obj, list):
        if field in _LIST_PROPS:
            return _LIST_PROPS[field](obj, line)
        if field in _LIST_METHODS:
            return _bind_method(_LIST_METHODS[field], obj)
        all_names = list(_LIST_PROPS) + list(_LIST_METHODS)
        raise PlainError(
            f"lists have no member `{field}`",
            line=line,
            hint=f"available: {', '.join(all_names)}",
        )
    if isinstance(obj, str):
        if field in _TEXT_PROPS:
            return _TEXT_PROPS[field](obj, line)
        if field in _TEXT_METHODS:
            return _bind_method(_TEXT_METHODS[field], obj)
        all_names = list(_TEXT_PROPS) + list(_TEXT_METHODS)
        raise PlainError(
            f"text has no member `{field}`",
            line=line,
            hint=f"available: {', '.join(all_names)}",
        )
    raise PlainError(
        f"`.{field}` not supported on {type_name(obj)}",
        line=line,
    )


def _bind_method(method_fn, receiver):
    """Wrap a builtin so that `receiver.method(args)` calls method_fn(receiver, args)."""
    name = method_fn.__name__
    def bound(args, line):
        return method_fn(receiver, args, line)
    return BuiltinFunc(name, bound)


def _eval_index(n: Index, env: Env):
    obj = evaluate(n.obj, env)
    key = evaluate(n.key, env)
    if isinstance(obj, list):
        _check_number(key, n.line, "list index")
        if key != key.to_integral_value():
            raise PlainError("list indices must be whole numbers", line=n.line)
        idx = int(key)
        # 1-based indexing for friendliness
        if idx < 1 or idx > len(obj):
            raise PlainError(
                f"index {idx} is out of range; list has {len(obj)} item(s)",
                line=n.line,
                hint="indexing starts at 1; use `list.first` or `list.last` for ends",
            )
        return obj[idx - 1]
    if isinstance(obj, PlainRecord):
        if not isinstance(key, str):
            raise PlainError(
                f"records are looked up by text keys, got {type_name(key)}",
                line=n.line,
            )
        # Indexing a record returns nothing on miss (vs. dot-access which errors)
        return obj.fields.get(key, NOTHING)
    if isinstance(obj, str):
        _check_number(key, n.line, "text index")
        if key != key.to_integral_value():
            raise PlainError("text indices must be whole numbers", line=n.line)
        idx = int(key)
        if idx < 1 or idx > len(obj):
            raise PlainError(
                f"index {idx} is out of range; text has {len(obj)} character(s)",
                line=n.line,
            )
        return obj[idx - 1]
    raise PlainError(
        f"can't index {type_name(obj)} with [...]",
        line=n.line,
    )


def _eval_if_expr(n: IfExpr, env: Env):
    cond = evaluate(n.cond, env)
    _check_bool(cond, n.line, "`if` condition")
    if cond:
        return evaluate(n.then_val, env)
    for ec, ev in n.elifs:
        c = evaluate(ec, env)
        _check_bool(c, n.line, "`else if` condition")
        if c:
            return evaluate(ev, env)
    return evaluate(n.else_val, env)


def _eval_pipeline(n: Pipeline, env: Env):
    value = evaluate(n.source, env)
    for stage in n.stages:
        value = _apply_stage(stage, value, env)
    return value


def _apply_stage(stage: PipelineStage, value, env: Env):
    if not isinstance(value, list):
        raise PlainError(
            f"`| {stage.verb}` only works on lists, got {type_name(value)}",
            line=stage.line,
            hint="convert to a list first, or pipeline a list of records",
        )

    if stage.verb == "where":
        out = []
        for item in value:
            inner = _scope_for_item(env, item)
            ok = evaluate(stage.arg, inner)
            _check_bool(ok, stage.line, "`where` predicate")
            if ok:
                out.append(item)
        return out

    if stage.verb == "sort":
        keyed = []
        for item in value:
            inner = _scope_for_item(env, item)
            k = evaluate(stage.arg, inner)
            keyed.append((k, item))
        # Validate sortable
        sample = keyed[0][0] if keyed else None
        for k, _ in keyed:
            if not (_is_number(k) or isinstance(k, str)):
                raise PlainError(
                    f"`sort by` keys must be numbers or text, got {type_name(k)}",
                    line=stage.line,
                )
        keyed.sort(key=lambda p: p[0], reverse=stage.descending)
        return [item for _, item in keyed]

    if stage.verb == "take":
        n_val = evaluate(stage.arg, env)
        _check_number(n_val, stage.line, "`take` count")
        if n_val != n_val.to_integral_value() or n_val < 0:
            raise PlainError(
                "`take` needs a whole non-negative number",
                line=stage.line,
            )
        return value[:int(n_val)]

    if stage.verb == "map":
        out = []
        for item in value:
            inner = _scope_for_item(env, item)
            out.append(evaluate(stage.arg, inner))
        return out

    if stage.verb == "group":
        groups: dict = {}
        order: list = []
        for item in value:
            inner = _scope_for_item(env, item)
            k = evaluate(stage.arg, inner)
            # Records and lists aren't hashable; key them by a structural form.
            dict_key = (type(k).__name__, repr(k)) if isinstance(k, (PlainRecord, list)) else k
            if dict_key not in groups:
                groups[dict_key] = (k, [])
                order.append(dict_key)
            groups[dict_key][1].append(item)
        key_field = stage.group_key_name or "key"
        out = []
        for dk in order:
            k_val, items = groups[dk]
            out.append(_GroupRecord({key_field: k_val, "items": items}))
        return out

    if stage.verb == "summarize":
        record_node: Record = stage.arg
        is_grouped = bool(value) and all(isinstance(x, _GroupRecord) for x in value)

        def compute(items_list, extra_fields=None):
            inner = Env(env)
            # Bind every field common to all items as a column-list, so users
            # can write `sum(amount)` over the group.
            if items_list and all(isinstance(x, PlainRecord) for x in items_list):
                common = set(items_list[0].fields.keys())
                for x in items_list[1:]:
                    common &= set(x.fields.keys())
                for fname in common:
                    inner.define(fname, [x.fields[fname] for x in items_list], mutable=False)
            inner.define("items", items_list, mutable=False)
            out_fields: dict = {}
            if extra_fields:
                for k, v in extra_fields.items():
                    out_fields[k] = v
            for fname, fexpr in record_node.fields:
                if fname in out_fields:
                    raise PlainError(
                        f"summarize has duplicate field `{fname}`",
                        line=stage.line,
                    )
                out_fields[fname] = evaluate(fexpr, inner)
            return PlainRecord(out_fields)

        if is_grouped:
            results = []
            for grp in value:
                items = grp.fields.get("items", [])
                if not isinstance(items, list):
                    raise PlainError(
                        "summarize: each group must carry an `items` list",
                        line=stage.line,
                    )
                # Carry the group key fields through (everything but `items`).
                extra = {k: v for k, v in grp.fields.items() if k != "items"}
                results.append(compute(items, extra_fields=extra))
            return results
        # Flat list -> single summary record. This is a terminal shape;
        # any verb chained after will error with "needs a list".
        return compute(value)

    raise PlainError(f"unknown pipeline verb `{stage.verb}`", line=stage.line)


def _scope_for_item(parent: Env, item) -> Env:
    inner = Env(parent)
    inner.define("it", item, mutable=False)
    if isinstance(item, PlainRecord):
        for k, v in item.fields.items():
            # Don't shadow `it` and don't override existing names -- record fields
            # take precedence over outer-scope names in pipeline expressions.
            try:
                inner.define(k, v, mutable=False)
            except PlainError:
                pass
    return inner


# ----- statements -----

def _eval_let(n: Let, env: Env):
    val = evaluate(n.value, env)
    env.define(n.name, val, n.mutable, line=n.line)

def _eval_assign(n: Assign, env: Env):
    val = evaluate(n.value, env)
    env.assign(n.name, val, line=n.line)

def _eval_if(n: If, env: Env):
    cond = evaluate(n.cond, env)
    _check_bool(cond, n.line, "`if` condition")
    if cond:
        _exec_block(n.then, env)
        return
    for ec, eb in n.elifs:
        c = evaluate(ec, env)
        _check_bool(c, n.line, "`else if` condition")
        if c:
            _exec_block(eb, env)
            return
    if n.else_:
        _exec_block(n.else_, env)

def _exec_block(stmts: list, parent: Env):
    inner = Env(parent)
    for s in stmts:
        evaluate(s, inner)

def _eval_for(n: For, env: Env):
    iterable = evaluate(n.iter, env)
    if not isinstance(iterable, list):
        raise PlainError(
            f"`for each` needs a list, got {type_name(iterable)}",
            line=n.line,
            hint="use a list literal `[1,2,3]`, a range `1..5`, or a function that returns a list",
        )
    for item in iterable:
        inner = Env(env)
        inner.define(n.name, item, mutable=False)
        for s in n.body:
            evaluate(s, inner)

def _eval_repeat(n: Repeat, env: Env):
    count = evaluate(n.count, env)
    _check_number(count, n.line, "`repeat` count")
    if count != count.to_integral_value() or count < 0:
        raise PlainError(
            "`repeat` needs a whole non-negative number",
            line=n.line,
        )
    for _ in range(int(count)):
        _exec_block(n.body, env)

def _eval_funcdef(n: FuncDef, env: Env):
    fn = PlainFunc(n.name, n.params, n.body, env)
    env.define(n.name, fn, mutable=False, line=n.line)

def _eval_return(n: Return_, env: Env):
    raise _Return(evaluate(n.value, env))

def _eval_try(n: Try_, env: Env):
    try:
        _exec_block(n.body, env)
    except PlainError as e:
        inner = Env(env)
        inner.define(n.err_name,
                     PlainRecord({"message": e.message,
                                  "line": Decimal(e.line) if e.line else NOTHING}),
                     mutable=False, line=n.line)
        for s in n.otherwise:
            evaluate(s, inner)

def _eval_show(n: Show, env: Env):
    val = evaluate(n.expr, env)
    print(format_value(val))

def _eval_exprstmt(n: ExprStmt, env: Env):
    evaluate(n.expr, env)


_DISPATCH = {
    Num: _eval_num, Str: _eval_str, FString: _eval_fstring,
    Bool: _eval_bool, Nothing: _eval_nothing,
    Name: _eval_name, List_: _eval_list, Record: _eval_record, Range_: _eval_range,
    UnaryOp: _eval_unary, BinOp: _eval_binop, Call: _eval_call,
    FieldAccess: _eval_field, Index: _eval_index, Pipeline: _eval_pipeline,
    IfExpr: _eval_if_expr,
    Let: _eval_let, Assign: _eval_assign, If: _eval_if, For: _eval_for,
    Repeat: _eval_repeat, FuncDef: _eval_funcdef, Return_: _eval_return,
    Try_: _eval_try, Show: _eval_show, ExprStmt: _eval_exprstmt,
}


# ============================================================
#  Built-in methods (record / list / text)
# ============================================================

# Properties: take only the receiver, return a value (auto-called on `.foo`).
# Methods:    take (receiver, args, line) and return a callable bound function.

# --- record properties ---
def _rp_keys(rcv: PlainRecord, line):  return list(rcv.fields.keys())

_RECORD_PROPS = {"keys": _rp_keys}

# --- record methods ---
def _rm_get(rcv: PlainRecord, args, line):
    if len(args) != 1 or not isinstance(args[0], str):
        raise PlainError("record.get(key) needs one text argument", line=line)
    return rcv.fields.get(args[0], NOTHING)

def _rm_with(rcv: PlainRecord, args, line):
    if len(args) == 1 and isinstance(args[0], PlainRecord):
        new = dict(rcv.fields); new.update(args[0].fields)
        return PlainRecord(new)
    if len(args) == 2 and isinstance(args[0], str):
        new = dict(rcv.fields); new[args[0]] = args[1]
        return PlainRecord(new)
    raise PlainError(
        'record.with needs a record like `{name: "Ada"}` or two args ("name", "Ada")',
        line=line,
    )

def _rm_has(rcv: PlainRecord, args, line):
    if len(args) != 1 or not isinstance(args[0], str):
        raise PlainError("record.has(key) needs one text argument", line=line)
    return args[0] in rcv.fields

_RECORD_METHODS = {"get": _rm_get, "with": _rm_with, "has": _rm_has}


# --- list properties ---
def _lp_first(rcv, line):    return rcv[0] if rcv else NOTHING
def _lp_last(rcv, line):     return rcv[-1] if rcv else NOTHING
def _lp_reverse(rcv, line):  return list(reversed(rcv))
def _lp_length(rcv, line):   return Decimal(len(rcv))

_LIST_PROPS = {
    "first": _lp_first, "last": _lp_last, "reverse": _lp_reverse, "length": _lp_length,
}

# --- list methods ---
def _lm_contains(rcv, args, line):
    if len(args) != 1:
        raise PlainError("list.contains(value) needs one argument", line=line)
    return args[0] in rcv

def _lm_join(rcv, args, line):
    if len(args) != 1 or not isinstance(args[0], str):
        raise PlainError("list.join(separator) needs one text argument", line=line)
    parts = []
    for x in rcv:
        if not isinstance(x, str):
            raise PlainError(
                f"list.join needs a list of text, found {type_name(x)} ({format_value(x)})",
                line=line,
            )
        parts.append(x)
    return args[0].join(parts)

_LIST_METHODS = {"contains": _lm_contains, "join": _lm_join}


# --- text properties ---
def _tp_upper(rcv, line):   return rcv.upper()
def _tp_lower(rcv, line):   return rcv.lower()
def _tp_trim(rcv, line):    return rcv.strip()
def _tp_length(rcv, line):  return Decimal(len(rcv))

_TEXT_PROPS = {
    "upper": _tp_upper, "lower": _tp_lower, "trim": _tp_trim, "length": _tp_length,
}

# --- text methods ---
def _tm_contains(rcv, args, line):
    if len(args) != 1 or not isinstance(args[0], str):
        raise PlainError("text.contains needs one text argument", line=line)
    return args[0] in rcv

def _tm_split(rcv, args, line):
    if len(args) != 1 or not isinstance(args[0], str):
        raise PlainError("text.split needs one text argument", line=line)
    return rcv.split(args[0]) if args[0] else list(rcv)

def _tm_starts_with(rcv, args, line):
    if len(args) != 1 or not isinstance(args[0], str):
        raise PlainError("text.starts_with needs one text argument", line=line)
    return rcv.startswith(args[0])

def _tm_ends_with(rcv, args, line):
    if len(args) != 1 or not isinstance(args[0], str):
        raise PlainError("text.ends_with needs one text argument", line=line)
    return rcv.endswith(args[0])

_TEXT_METHODS = {
    "contains": _tm_contains, "split": _tm_split,
    "starts_with": _tm_starts_with, "ends_with": _tm_ends_with,
}


# ============================================================
#  Top-level built-ins
# ============================================================

def _b_length(args, line):
    v = args[0]
    if isinstance(v, (str, list)):
        return Decimal(len(v))
    if isinstance(v, PlainRecord):
        return Decimal(len(v.fields))
    raise PlainError(f"length() doesn't work on {type_name(v)}", line=line)

def _b_to_text(args, line):
    return format_value(args[0])

def _b_to_number(args, line):
    v = args[0]
    if isinstance(v, bool):
        raise PlainError("can't convert true/false to a number", line=line)
    if _is_number(v):
        return v
    if isinstance(v, str):
        try:
            return Decimal(v.strip())
        except InvalidOperation:
            raise PlainError(
                f"can't read {format_value(v)!r} as a number",
                line=line,
                hint="text must look like `42` or `3.14`",
            )
    raise PlainError(f"can't convert {type_name(v)} to a number", line=line)

def _b_sum(args, line):
    if not isinstance(args[0], list):
        raise PlainError(f"sum() needs a list, got {type_name(args[0])}", line=line)
    total = Decimal(0)
    for x in args[0]:
        if not _is_number(x):
            raise PlainError(
                f"sum() needs a list of numbers; found {type_name(x)} ({format_value(x)})",
                line=line,
            )
        total += x
    return total

def _b_count(args, line):
    if not isinstance(args[0], list):
        raise PlainError(f"count() needs a list, got {type_name(args[0])}", line=line)
    return Decimal(len(args[0]))

def _b_average(args, line):
    nums = args[0]
    if not isinstance(nums, list):
        raise PlainError(f"average() needs a list, got {type_name(nums)}", line=line)
    if not nums:
        raise PlainError("average() of an empty list is undefined", line=line)
    return _b_sum([nums], line) / Decimal(len(nums))

def _b_min(args, line):
    nums = args[0]
    if not isinstance(nums, list) or not nums:
        raise PlainError("min() needs a non-empty list", line=line)
    for x in nums:
        if not _is_number(x):
            raise PlainError(f"min() needs numbers, found {type_name(x)}", line=line)
    return min(nums)

def _b_max(args, line):
    nums = args[0]
    if not isinstance(nums, list) or not nums:
        raise PlainError("max() needs a non-empty list", line=line)
    for x in nums:
        if not _is_number(x):
            raise PlainError(f"max() needs numbers, found {type_name(x)}", line=line)
    return max(nums)

def _b_range(args, line):
    if len(args) == 1:
        start, end = Decimal(1), args[0]
    else:
        start, end = args[0], args[1]
    _check_number(start, line, "range start"); _check_number(end, line, "range end")
    if start != start.to_integral_value() or end != end.to_integral_value():
        raise PlainError("range() needs whole numbers", line=line)
    s, e = int(start), int(end)
    if s <= e:
        return [Decimal(x) for x in range(s, e + 1)]
    return [Decimal(x) for x in range(s, e - 1, -1)]

def _b_keys(args, line):
    v = args[0]
    if isinstance(v, PlainRecord):
        return list(v.fields.keys())
    raise PlainError(f"keys() needs a record, got {type_name(v)}", line=line)

def _b_type(args, line):
    return type_name(args[0])

def _b_assert(args, line):
    cond = args[0]
    msg = args[1] if len(args) > 1 else "assertion failed"
    _check_bool(cond, line, "assert condition")
    if not cond:
        raise PlainError(str(msg), line=line)
    return NOTHING

# math module
def _m_sqrt(args, line):
    n = _check_number(args[0], line, "math.sqrt")
    if n < 0:
        raise PlainError("math.sqrt of a negative number is undefined", line=line)
    return n.sqrt()

def _m_abs(args, line):
    return abs(_check_number(args[0], line, "math.abs"))

def _m_floor(args, line):
    return Decimal(math.floor(_check_number(args[0], line, "math.floor")))

def _m_ceil(args, line):
    return Decimal(math.ceil(_check_number(args[0], line, "math.ceil")))

def _m_round(args, line):
    n = _check_number(args[0], line, "math.round")
    # Python's built-in round() on Decimal returns int with banker's rounding;
    # cast straight back so the cr8script value stays a number.
    return Decimal(round(n))

def _m_pow(args, line):
    a = _check_number(args[0], line, "math.pow base")
    b = _check_number(args[1], line, "math.pow exponent")
    if b == b.to_integral_value():
        # Exact integer exponent -- stay in Decimal land.
        return a ** int(b)
    # Fractional exponent: fall back to binary float math, then re-import
    # via repr() so the shortest faithful decimal representation wins.
    return Decimal(repr(float(a) ** float(b)))


# --- http module ---
def _http_get(args, line):
    url = args[0]
    if not isinstance(url, str):
        raise PlainError(f"http.get needs a text URL, got {type_name(url)}", line=line)
    import urllib.request, urllib.error, time as _time
    req = urllib.request.Request(url, headers={"User-Agent": "cr8script/0.1"})
    t0 = _time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = _num((_time.monotonic() - t0) * 1000.0)
            return PlainRecord({
                "ok": True,
                "status": Decimal(resp.status),
                "body": body,
                "time_ms": elapsed_ms,
                "error": NOTHING,
            })
    except urllib.error.HTTPError as e:
        elapsed_ms = _num((_time.monotonic() - t0) * 1000.0)
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        # ok==True: we got an HTTP reply at all (even if 4xx/5xx).
        # User decides what counts as success via status.
        return PlainRecord({
            "ok": True,
            "status": Decimal(e.code),
            "body": body,
            "time_ms": elapsed_ms,
            "error": NOTHING,
        })
    except Exception as e:
        elapsed_ms = _num((_time.monotonic() - t0) * 1000.0)
        return PlainRecord({
            "ok": False,
            "status": Decimal(0),
            "body": "",
            "time_ms": elapsed_ms,
            "error": str(e),
        })


# --- json module ---
def _from_json_value(v):
    if v is None:
        return NOTHING
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return v
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return [_from_json_value(x) for x in v]
    if isinstance(v, dict):
        return PlainRecord({k: _from_json_value(x) for k, x in v.items()})
    raise PlainError(f"json: unsupported value of type {type(v).__name__}")


def _to_json_value(v, line):
    if v is NOTHING:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        if v.is_nan():
            raise PlainError("can't write NaN as JSON", line=line)
        if v.is_infinite():
            raise PlainError("can't write infinity as JSON", line=line)
        # Integer-valued numbers serialize without the ".0" tail to match
        # cr8script's display convention (1.0 shows as "1").
        if v == v.to_integral_value() and abs(v) < Decimal(10) ** 16:
            return int(v)
        # A bare Decimal isn't JSON-serializable. format_value gives the
        # canonical short form; wrap with a tiny encoder later. We pre-stringify
        # the value here as a JSON-raw token via a custom encoder below.
        return _RawJSON(format_value(v))
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return [_to_json_value(x, line) for x in v]
    if isinstance(v, PlainRecord):
        return {k: _to_json_value(x, line) for k, x in v.fields.items()}
    raise PlainError(
        f"can't write {type_name(v)} as JSON",
        line=line,
        hint="JSON supports text, numbers, true/false, nothing, lists, and records",
    )


class _RawJSON:
    """Sentinel: emit the wrapped string as raw JSON (used for Decimal numbers)."""
    __slots__ = ("text",)
    def __init__(self, text): self.text = text


class _RawJSONEncoder:
    """Re-emit Python's json with our raw-decimal sentinels inlined."""
    @staticmethod
    def encode(obj, indent=None):
        import json as _json
        # Replace _RawJSON markers with unique placeholders, then patch.
        slots: list[str] = []
        def replace(o):
            if isinstance(o, _RawJSON):
                slots.append(o.text)
                return f"@@RAWJSON_{len(slots) - 1}@@"
            if isinstance(o, list):
                return [replace(x) for x in o]
            if isinstance(o, dict):
                return {k: replace(v) for k, v in o.items()}
            return o
        prepared = replace(obj)
        text = _json.dumps(prepared, indent=indent, ensure_ascii=False)
        for i, raw in enumerate(slots):
            text = text.replace(f'"@@RAWJSON_{i}@@"', raw)
        return text


def _j_parse(args, line):
    text = args[0]
    if not isinstance(text, str):
        raise PlainError(f"json.parse needs text, got {type_name(text)}", line=line)
    import json as _json
    try:
        v = _json.loads(text, parse_float=Decimal, parse_int=Decimal)
    except _json.JSONDecodeError as e:
        raise PlainError(
            f"json.parse failed: {e.msg}",
            line=line,
            hint=f"check around character {e.pos} for unbalanced quotes/brackets or stray commas",
        )
    return _from_json_value(v)


def _j_stringify(args, line):
    indent = None
    if len(args) >= 2:
        n = _check_number(args[1], line, "json.stringify indent")
        if n != n.to_integral_value() or n < 0:
            raise PlainError(
                "json.stringify indent must be a whole non-negative number",
                line=line,
            )
        indent = int(n)
    return _RawJSONEncoder.encode(_to_json_value(args[0], line), indent=indent)


# --- csv module ---
def _c_parse(args, line):
    text = args[0]
    if not isinstance(text, str):
        raise PlainError(f"csv.parse needs text, got {type_name(text)}", line=line)
    import csv as _csv, io as _io
    reader = _csv.reader(_io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    headers = [h for h in rows[0]]
    if len(headers) != len(set(headers)):
        seen = set(); dup = None
        for h in headers:
            if h in seen:
                dup = h; break
            seen.add(h)
        raise PlainError(
            f"csv.parse: duplicate header `{dup}`",
            line=line,
            hint="rename the duplicated column in the source CSV",
        )
    out = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != len(headers):
            raise PlainError(
                f"csv.parse: row {i} has {len(row)} field(s), header has {len(headers)}",
                line=line,
                hint="check for stray commas or unquoted commas inside a value",
            )
        out.append(PlainRecord(dict(zip(headers, row))))
    return out


def _c_write(args, line):
    rows = args[0]
    if not isinstance(rows, list):
        raise PlainError(f"csv.write needs a list of records, got {type_name(rows)}", line=line)
    if not rows:
        return ""
    # Header is the union of keys, ordered by first appearance.
    headers: list = []
    seen: set = set()
    for r in rows:
        if not isinstance(r, PlainRecord):
            raise PlainError(
                f"csv.write needs a list of records; found {type_name(r)} ({format_value(r)})",
                line=line,
            )
        for k in r.fields:
            if k not in seen:
                seen.add(k); headers.append(k)
    import csv as _csv, io as _io
    buf = _io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for r in rows:
        row = []
        for h in headers:
            v = r.fields.get(h, NOTHING)
            if v is NOTHING:
                row.append("")
            elif isinstance(v, bool):
                row.append("true" if v else "false")
            elif isinstance(v, str):
                row.append(v)
            elif _is_number(v):
                row.append(format_value(v))
            else:
                raise PlainError(
                    f"csv.write can't write {type_name(v)} ({format_value(v)}) -- "
                    f"only text, numbers, true/false, and nothing fit in a CSV cell",
                    line=line,
                )
        writer.writerow(row)
    return buf.getvalue()


# --- time module ---
def _t_now(args, line):
    import time as _time
    return _num(_time.time())

def _t_monotonic(args, line):
    import time as _time
    return _num(_time.monotonic())

def _t_sleep(args, line):
    import time as _time
    s = _check_number(args[0], line, "time.sleep seconds")
    if s < 0:
        raise PlainError("time.sleep needs a non-negative number", line=line)
    _time.sleep(float(s))
    return NOTHING


# Holds CLI args passed after the script path.
_SCRIPT_ARGS: list[str] = []


def make_global_env() -> Env:
    # Built-ins live in a parent scope so user code can shadow them
    # (e.g. `var sum = 0` is allowed; it just hides the built-in `sum` locally).
    builtins_env = Env()
    def add(name, fn, arity=None):
        builtins_env.define(name, BuiltinFunc(name, fn, arity), mutable=False)

    add("length",     _b_length,    (1, 1))
    add("to_text",    _b_to_text,   (1, 1))
    add("to_number",  _b_to_number, (1, 1))
    add("sum",        _b_sum,       (1, 1))
    add("count",      _b_count,     (1, 1))
    add("average",    _b_average,   (1, 1))
    add("min",        _b_min,       (1, 1))
    add("max",        _b_max,       (1, 1))
    add("range",      _b_range,     (1, 2))
    add("keys",       _b_keys,      (1, 1))
    add("type",       _b_type,      (1, 1))
    add("assert",     _b_assert,    (1, 2))

    math_mod = PlainModule("math", {
        "sqrt":  BuiltinFunc("math.sqrt",  _m_sqrt,  (1, 1)),
        "abs":   BuiltinFunc("math.abs",   _m_abs,   (1, 1)),
        "floor": BuiltinFunc("math.floor", _m_floor, (1, 1)),
        "ceil":  BuiltinFunc("math.ceil",  _m_ceil,  (1, 1)),
        "round": BuiltinFunc("math.round", _m_round, (1, 1)),
        "pow":   BuiltinFunc("math.pow",   _m_pow,   (2, 2)),
        # Constants are exposed as cr8 numbers (Decimal), not Python floats,
        # so arithmetic with them obeys the same one-number-type rule as
        # everything else. Decimal(str(...)) gives the printable IEEE-754
        # representation rather than the binary expansion.
        "pi":    Decimal(str(math.pi)),
        "e":     Decimal(str(math.e)),
    })
    builtins_env.define("math", math_mod, mutable=False)

    http_mod = PlainModule("http", {
        "get": BuiltinFunc("http.get", _http_get, (1, 1)),
    })
    builtins_env.define("http", http_mod, mutable=False)

    json_mod = PlainModule("json", {
        "parse":     BuiltinFunc("json.parse",     _j_parse,     (1, 1)),
        "stringify": BuiltinFunc("json.stringify", _j_stringify, (1, 2)),
    })
    builtins_env.define("json", json_mod, mutable=False)

    csv_mod = PlainModule("csv", {
        "parse": BuiltinFunc("csv.parse", _c_parse, (1, 1)),
        "write": BuiltinFunc("csv.write", _c_write, (1, 1)),
    })
    builtins_env.define("csv", csv_mod, mutable=False)

    time_mod = PlainModule("time", {
        "now":       BuiltinFunc("time.now",       _t_now,       (0, 0)),
        "monotonic": BuiltinFunc("time.monotonic", _t_monotonic, (0, 0)),
        "sleep":     BuiltinFunc("time.sleep",     _t_sleep,     (1, 1)),
    })
    builtins_env.define("time", time_mod, mutable=False)

    # CLI args passed after the script path: e.g.
    #   python3 cr8script.py load_test.cr8 http://example.com 50
    builtins_env.define("args", list(_SCRIPT_ARGS), mutable=False)

    return Env(builtins_env)


# ============================================================
#  Static checker (best-effort, AST-only, no execution)
# ============================================================

# Names valid on every record/list/text receiver, in addition to user fields.
_RECORD_BUILTIN_NAMES = set(_RECORD_PROPS) | set(_RECORD_METHODS)
_LIST_BUILTIN_NAMES = set(_LIST_PROPS) | set(_LIST_METHODS)
_TEXT_BUILTIN_NAMES = set(_TEXT_PROPS) | set(_TEXT_METHODS)


class _RecordShape:
    __slots__ = ("fields",)
    def __init__(self, fields):
        self.fields = set(fields)


class _ListShape:
    __slots__ = ("item",)
    def __init__(self, item):
        self.item = item  # _RecordShape | None


class _CheckIssue:
    __slots__ = ("severity", "message", "line", "hint")
    def __init__(self, message, line, hint=None, severity="error"):
        self.severity = severity
        self.message = message
        self.line = line
        self.hint = hint

    def to_dict(self):
        return {
            "severity": self.severity,
            "line": self.line,
            "message": self.message,
            "hint": self.hint,
        }

    def format_friendly(self):
        loc = f" (line {self.line})" if self.line is not None else ""
        out = f"{self.severity}{loc}: {self.message}"
        if self.hint:
            out += f"\n  hint: {self.hint}"
        return out


_BUILTIN_NAMES = frozenset({
    # Top-level functions registered in make_global_env.
    "length", "to_text", "to_number", "sum", "count", "average",
    "min", "max", "range", "keys", "type", "assert",
    # Modules.
    "math", "http", "json", "csv", "time",
    # Special.
    "args",
})


class Checker:
    """Walks the AST emitting issues without executing anything.

    Tracks let/var bindings to record literals and lists of record literals so
    `r.typo` and `things | where typo is ...` are flagged before runtime.
    Also validates bare-name references against scope + builtins so an undefined
    identifier (including a typo'd field inside a pipeline stage) is caught
    before run time.
    """

    def __init__(self):
        self.issues: list[_CheckIssue] = []
        self.scope: list[dict] = [{}]
        # Counter for "permissive" frames where bare-name validation is
        # skipped -- pipeline / summarize stages whose item shape isn't
        # statically known. Bare names there might resolve at runtime to
        # fields we couldn't determine, so we shouldn't flag them.
        self.permissive_depth: int = 0

    # --- scope helpers ---
    def push(self):
        self.scope.append({})

    def pop(self):
        self.scope.pop()

    def define(self, name, shape):
        self.scope[-1][name] = shape

    def lookup(self, name):
        for s in reversed(self.scope):
            if name in s:
                return s[name]
        return None

    def is_defined(self, name) -> bool:
        for s in self.scope:
            if name in s:
                return True
        return name in _BUILTIN_NAMES

    def visible_names(self) -> list:
        seen = set(_BUILTIN_NAMES)
        for s in self.scope:
            seen.update(s.keys())
        return list(seen)

    def issue(self, message, line, hint=None, severity="error"):
        self.issues.append(_CheckIssue(message, line, hint, severity))

    # --- entry ---
    def check_program(self, stmts: list):
        # Pre-pass: register top-level function names so mutual recursion
        # and forward references (a function that calls one defined later
        # in the file) don't false-positive against the bare-name check.
        # Top-level `let` bindings are *not* pre-registered -- `let x = x`
        # should still flag x as undefined.
        for s in stmts:
            if type(s) is FuncDef:
                self.define(s.name, None)
        for s in stmts:
            self.check_stmt(s)

    # --- statements ---
    def check_stmt(self, node):
        t = type(node)
        if t is Let:
            shape = self.shape_of(node.value)
            self.check_expr(node.value)
            self.define(node.name, shape if not node.mutable else None)
            return
        if t is Assign:
            self.check_expr(node.value)
            # Mutable rebinds lose tracked shape -- be conservative.
            for s in self.scope:
                if node.name in s:
                    s[node.name] = None
                    break
            return
        if t is If:
            self.check_expr(node.cond)
            self._check_block(node.then)
            for ec, eb in node.elifs:
                self.check_expr(ec)
                self._check_block(eb)
            if node.else_:
                self._check_block(node.else_)
            return
        if t is For:
            self.check_expr(node.iter)
            iter_shape = self.shape_of(node.iter)
            item_shape = iter_shape.item if isinstance(iter_shape, _ListShape) else None
            self.push()
            self.define(node.name, item_shape)
            for s in node.body:
                self.check_stmt(s)
            self.pop()
            return
        if t is Repeat:
            self.check_expr(node.count)
            self._check_block(node.body)
            return
        if t is FuncDef:
            self.define(node.name, None)
            self.push()
            for p in node.params:
                self.define(p, None)
            for s in node.body:
                self.check_stmt(s)
            self.pop()
            return
        if t is Return_:
            self.check_expr(node.value)
            return
        if t is Try_:
            # Walk into `try` bodies -- a typo wrapped in `try` is still a typo.
            # Programs that genuinely *want* a missing field can use indexing
            # (`r["x"]`), which returns nothing rather than erroring.
            self.push()
            for s in node.body:
                self.check_stmt(s)
            self.pop()
            self.push()
            self.define(node.err_name, _RecordShape({"message", "line"}))
            for s in node.otherwise:
                self.check_stmt(s)
            self.pop()
            return
        if t is Show:
            self.check_expr(node.expr)
            return
        if t is ExprStmt:
            self.check_expr(node.expr)
            return
        # Fall through: unknown statement type (shouldn't happen).

    def _check_block(self, stmts):
        self.push()
        for s in stmts:
            self.check_stmt(s)
        self.pop()

    # --- expressions ---
    def check_expr(self, node):
        if node is None:
            return
        t = type(node)
        if t in (Num, Str, Bool, Nothing):
            return
        if t is FString:
            for p in node.parts:
                if not isinstance(p, str):
                    self.check_expr(p)
            return
        if t is Name:
            if self.permissive_depth > 0:
                return
            if not self.is_defined(node.ident):
                suggest = _did_you_mean(node.ident, self.visible_names())
                hint = f"did you mean `{suggest}`?" if suggest else None
                self.issue(f"`{node.ident}` is not defined", node.line, hint=hint)
            return
        if t is List_:
            for x in node.items:
                self.check_expr(x)
            return
        if t is Record:
            for _, e in node.fields:
                self.check_expr(e)
            return
        if t is Range_:
            self.check_expr(node.start); self.check_expr(node.end); return
        if t is UnaryOp:
            self.check_expr(node.operand); return
        if t is BinOp:
            self.check_expr(node.left); self.check_expr(node.right); return
        if t is Call:
            self.check_expr(node.func)
            for a in node.args:
                self.check_expr(a)
            return
        if t is FieldAccess:
            self.check_expr(node.obj)
            self._check_field_access(node)
            return
        if t is Index:
            self.check_expr(node.obj); self.check_expr(node.key); return
        if t is Pipeline:
            self._check_pipeline(node)
            return
        if t is IfExpr:
            self.check_expr(node.cond)
            self.check_expr(node.then_val)
            for ec, ev in node.elifs:
                self.check_expr(ec); self.check_expr(ev)
            self.check_expr(node.else_val)
            return

    def _check_field_access(self, node: FieldAccess):
        shape = self.shape_of(node.obj)
        if not isinstance(shape, _RecordShape):
            return
        if node.field in shape.fields or node.field in _RECORD_BUILTIN_NAMES:
            return
        available = sorted(shape.fields)
        suggest = _did_you_mean(node.field, available)
        hint = f"available: {', '.join(available) or '(none)'}"
        if suggest:
            hint = f"did you mean `{suggest}`? " + hint
        self.issue(f"record has no field `{node.field}`", node.line, hint=hint)

    def _check_pipeline(self, node: Pipeline):
        self.check_expr(node.source)
        src_shape = self.shape_of(node.source)
        item_shape = src_shape.item if isinstance(src_shape, _ListShape) else None
        # `pre_group_shape` is what `summarize` sees as the per-group
        # items' element shape. Set whenever we last had a flat list of
        # records -- `group by` records the pre-group shape so a later
        # `summarize` resolves bare names against the original fields.
        pre_group_shape = item_shape
        for stage in node.stages:
            if stage.verb == "summarize" and isinstance(stage.arg, Record):
                # summarize binds `items` plus every field common to the
                # items list. We approximate by binding everything in
                # `pre_group_shape` (the runtime intersects across all
                # items, but for a static check the pre-group shape is
                # already a tight upper bound).
                self.push()
                permissive = not isinstance(pre_group_shape, _RecordShape)
                if permissive:
                    self.permissive_depth += 1
                self.define("items", None)
                if isinstance(pre_group_shape, _RecordShape):
                    for f in pre_group_shape.fields:
                        self.define(f, None)
                for _, e in stage.arg.fields:
                    self.check_expr(e)
                if permissive:
                    self.permissive_depth -= 1
                self.pop()
            elif stage.field_scoped:
                self.push()
                permissive = not isinstance(item_shape, _RecordShape)
                if permissive:
                    self.permissive_depth += 1
                else:
                    for f in item_shape.fields:
                        self.define(f, None)
                self.define("it", item_shape)
                # Validate field accesses inside the stage expression
                self._check_pipeline_stage_expr(stage.arg, item_shape)
                if permissive:
                    self.permissive_depth -= 1
                self.pop()
            else:
                self.check_expr(stage.arg)
            # Update the tracked item shape for the next stage.
            if stage.verb == "map" and isinstance(stage.arg, Name) and isinstance(item_shape, _RecordShape):
                item_shape = None  # projected scalar -- no record shape
                pre_group_shape = None
            elif stage.verb == "group":
                key_name = stage.group_key_name or "key"
                item_shape = _RecordShape({key_name, "items"})
                # pre_group_shape stays as the prior flat-records shape
            elif stage.verb == "summarize":
                if isinstance(stage.arg, Record):
                    item_shape = _RecordShape(k for k, _ in stage.arg.fields)
                else:
                    item_shape = None
                pre_group_shape = item_shape

    def _check_pipeline_stage_expr(self, expr, item_shape):
        # When a bare Name in a field-scoped stage expression matches one of
        # the item's fields, that's a valid reference -- don't flag it.
        # Otherwise we walk normally so typos in `.field` chains still surface.
        if expr is None:
            return
        self.check_expr(expr)

    # --- shape inference (best-effort) ---
    def shape_of(self, node):
        if node is None:
            return None
        t = type(node)
        if t is Record:
            return _RecordShape(k for k, _ in node.fields)
        if t is List_:
            elem_shapes = [self.shape_of(x) for x in node.items]
            if elem_shapes and all(isinstance(s, _RecordShape) for s in elem_shapes):
                # Intersection of fields -- only fields present in *every*
                # element are guaranteed.
                common: Optional[set] = None
                for s in elem_shapes:
                    common = set(s.fields) if common is None else common & s.fields
                return _ListShape(_RecordShape(common or set()))
            return _ListShape(None)
        if t is Name:
            return self.lookup(node.ident)
        if t is Range_:
            return _ListShape(None)
        return None


def _did_you_mean(needle: str, haystack):
    """Return a single best match if it's close; else None. No deps."""
    if not haystack:
        return None
    def edit_distance(a, b):
        if a == b:
            return 0
        la, lb = len(a), len(b)
        if abs(la - lb) > 2:
            return 99
        prev = list(range(lb + 1))
        for i in range(1, la + 1):
            cur = [i] + [0] * lb
            for j in range(1, lb + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            prev = cur
        return prev[lb]
    best = min(haystack, key=lambda h: edit_distance(needle, h))
    if edit_distance(needle, best) <= 2:
        return best
    return None


def run_check(src: str, filename: str = "<input>", as_json: bool = False) -> int:
    """Lex, parse, and check; print issues. Return non-zero if any error issues."""
    try:
        tokens = tokenize(src)
        stmts = Parser(tokens).parse_program()
    except PlainError as e:
        if as_json:
            import json as _json
            print(_json.dumps([{
                "severity": "error",
                "line": e.line,
                "message": e.message,
                "hint": e.hint,
            }]))
        else:
            print(f"{filename}: {e.format()}", file=sys.stderr)
        return 1
    chk = Checker()
    chk.check_program(stmts)
    has_error = any(i.severity == "error" for i in chk.issues)
    if as_json:
        import json as _json
        print(_json.dumps([i.to_dict() for i in chk.issues]))
    else:
        for i in chk.issues:
            print(f"{filename}: {i.format_friendly()}", file=sys.stderr)
        if not chk.issues:
            print(f"{filename}: ok -- no issues", file=sys.stderr)
    return 1 if has_error else 0


# ============================================================
#  Driver: run / repl / tests
# ============================================================

def run_source(src: str, filename: str = "<input>") -> int:
    try:
        tokens = tokenize(src)
        stmts = Parser(tokens).parse_program()
        env = make_global_env()
        evaluate_program(stmts, env)
        return 0
    except PlainError as e:
        print(f"{filename}: {e.format()}", file=sys.stderr)
        return 1


def repl():
    env = make_global_env()
    print("cr8script REPL -- type expressions or statements; Ctrl-D to exit.")
    buf = ""
    prompt = ">>> "
    while True:
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return
        buf = (buf + "\n" + line).strip() if buf else line
        # Try to parse -- if it fails because of EOF mid-block, continue reading.
        try:
            tokens = tokenize(buf + "\n")
            stmts = Parser(tokens).parse_program()
        except PlainError as e:
            if "missing `end`" in e.message or e.message.startswith("expected"):
                if buf and not line.strip() == "":
                    prompt = "... "
                    continue
            print(e.format(), file=sys.stderr)
            buf = ""
            prompt = ">>> "
            continue
        # Evaluate; print last expr-statement value.
        try:
            for s in stmts:
                if isinstance(s, ExprStmt):
                    val = evaluate(s.expr, env)
                    if val is not NOTHING:
                        print(format_value(val))
                else:
                    evaluate(s, env)
        except PlainError as e:
            print(e.format(), file=sys.stderr)
        buf = ""
        prompt = ">>> "


def run_tests(testdir: str) -> int:
    failures = 0
    total = 0
    for name in sorted(os.listdir(testdir)):
        if not name.endswith(".cr8"):
            continue
        total += 1
        path = os.path.join(testdir, name)
        expected_path = path[:-4] + ".expected"
        if not os.path.exists(expected_path):
            print(f"SKIP  {name}  (no .expected file)")
            continue
        src = open(path).read()
        expected = open(expected_path).read()
        # Capture stdout/stderr
        import io, contextlib
        out = io.StringIO()
        err = io.StringIO()
        rc = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_source(src, filename=name)
        actual = out.getvalue() + err.getvalue()
        if actual.rstrip() == expected.rstrip():
            print(f"OK    {name}")
        else:
            failures += 1
            print(f"FAIL  {name}")
            print("  expected:")
            for ln in expected.rstrip().splitlines():
                print(f"    {ln}")
            print("  actual:")
            for ln in actual.rstrip().splitlines():
                print(f"    {ln}")
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


def _main_impl(argv) -> int:
    if len(argv) >= 2 and argv[1] == "--lex":
        src = sys.stdin.read() if len(argv) < 3 else open(argv[2]).read()
        for t in tokenize(src):
            print(t)
        return 0
    if len(argv) >= 2 and argv[1] == "--ast":
        src = open(argv[2]).read()
        stmts = Parser(tokenize(src)).parse_program()
        for s in stmts:
            print(s)
        return 0
    if len(argv) >= 2 and argv[1] in ("--check", "--check-json"):
        if len(argv) < 3:
            print("usage: cr8script.py --check <file>", file=sys.stderr)
            return 2
        path = argv[2]
        return run_check(open(path).read(),
                         filename=os.path.basename(path),
                         as_json=(argv[1] == "--check-json"))
    if len(argv) >= 2 and argv[1] == "--test":
        testdir = argv[2] if len(argv) >= 3 else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "testdata")
        return run_tests(testdir)
    if len(argv) >= 2:
        path = argv[1]
        global _SCRIPT_ARGS
        _SCRIPT_ARGS = list(argv[2:])
        return run_source(open(path).read(), filename=os.path.basename(path))
    repl()
    return 0


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv
    try:
        return _main_impl(argv)
    except PlainError as e:
        print(e.format(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
