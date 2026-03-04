# =============================================================================
#  FIRE Language Parser — Parse FIRE scripts into Abstract Syntax Trees
#
#  Implements the FIRE language grammar:
#    script    = operation { ("+" | "||") operation }
#    operation = atomic | composite | conditional | repeat | collection
#    atomic    = identifier "(" [ param { "," param } ] ")"
#    composite = "(" script ")"
#    conditional = "if" condition "then" script "else" script
#    repeat    = operation "*" number
#    collection = "Σ(" identifier "in" identifier ":" script ")"
#    condition = expression ("==" | "!=" | "<" | ">" | "<=" | ">=") expression
# =============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class NodeType(str, Enum):
    """Types of AST nodes in the FIRE language."""
    ATOMIC = "atomic"
    SEQUENCE = "sequence"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    REPEAT = "repeat"
    COLLECTION = "collection"
    VARIABLE = "variable"
    LITERAL = "literal"
    ASSIGNMENT = "assignment"
    COMPARISON = "comparison"


@dataclass
class ASTNode:
    """A node in the FIRE abstract syntax tree."""
    node_type: NodeType
    # For ATOMIC: op name; for VARIABLE: var name; for LITERAL: value
    value: Any = None
    children: List["ASTNode"] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    # For CONDITIONAL
    condition: Optional["ASTNode"] = None
    if_branch: Optional["ASTNode"] = None
    else_branch: Optional["ASTNode"] = None
    # For REPEAT
    count: int = 0
    # For COLLECTION
    var_name: str = ""
    collection_name: str = ""
    # For COMPARISON
    operator: str = ""
    # For ASSIGNMENT
    identifier: str = ""

    def __repr__(self) -> str:
        if self.node_type == NodeType.ATOMIC:
            param_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
            return f"{self.value}({param_str})"
        elif self.node_type == NodeType.SEQUENCE:
            return " + ".join(repr(c) for c in self.children)
        elif self.node_type == NodeType.PARALLEL:
            return " || ".join(repr(c) for c in self.children)
        elif self.node_type == NodeType.CONDITIONAL:
            return f"if {self.condition} then {self.if_branch} else {self.else_branch}"
        elif self.node_type == NodeType.REPEAT:
            return f"{self.children[0]} * {self.count}"
        elif self.node_type == NodeType.VARIABLE:
            return str(self.value)
        elif self.node_type == NodeType.LITERAL:
            return str(self.value)
        elif self.node_type == NodeType.ASSIGNMENT:
            return f"let {self.identifier} = {self.children[0]}"
        return f"<{self.node_type}>"


class FireParseError(Exception):
    """Raised when the parser encounters invalid FIRE syntax."""
    def __init__(self, message: str, position: int = -1):
        self.position = position
        super().__init__(f"Parse error at position {position}: {message}" if position >= 0 else message)


class _Tokenizer:
    """Simple tokenizer for the FIRE language."""

    # Token patterns (order matters — longer matches first)
    _PATTERNS = [
        ("COMMENT",   r"//[^\n]*"),
        ("WHITESPACE", r"\s+"),
        ("HEX",       r"0x[0-9a-fA-F]+"),
        ("NUMBER",    r"\d+(?:\.\d+)?"),
        ("STRING",    r'"[^"]*"'),
        ("LET",       r"\blet\b"),
        ("IF",        r"\bif\b"),
        ("THEN",      r"\bthen\b"),
        ("ELSE",      r"\belse\b"),
        ("IN",        r"\bin\b"),
        ("PARALLEL",  r"\|\|"),
        ("COMPARISON", r"==|!=|<=|>=|<|>"),
        ("SIGMA",     r"Σ"),
        ("PLUS",      r"\+"),
        ("STAR",      r"\*"),
        ("TILDE",     r"~"),
        ("ARROW",     r"→|->"),
        ("EQUALS",    r"="),
        ("LPAREN",    r"\("),
        ("RPAREN",    r"\)"),
        ("COMMA",     r","),
        ("COLON",     r":"),
        ("IDENT",     r"[A-Za-z_][A-Za-z0-9_]*"),
    ]

    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.tokens: List[tuple] = []
        self._tokenize()
        self._index = 0

    def _tokenize(self) -> None:
        combined = "|".join(f"(?P<{name}>{pattern})" for name, pattern in self._PATTERNS)
        regex = re.compile(combined)
        for m in regex.finditer(self.source):
            kind = m.lastgroup
            if kind in ("WHITESPACE", "COMMENT"):
                continue
            self.tokens.append((kind, m.group(), m.start()))

    def peek(self) -> Optional[tuple]:
        if self._index < len(self.tokens):
            return self.tokens[self._index]
        return None

    def advance(self) -> tuple:
        tok = self.tokens[self._index]
        self._index += 1
        return tok

    def expect(self, kind: str) -> tuple:
        tok = self.peek()
        if tok is None:
            raise FireParseError(f"Expected {kind}, got end of input", len(self.source))
        if tok[0] != kind:
            raise FireParseError(f"Expected {kind}, got {tok[0]} ('{tok[1]}')", tok[2])
        return self.advance()

    def match(self, kind: str) -> Optional[tuple]:
        tok = self.peek()
        if tok and tok[0] == kind:
            return self.advance()
        return None

    @property
    def at_end(self) -> bool:
        return self._index >= len(self.tokens)


class FireParser:
    """Parser for the FIRE scripting language.

    Parses FIRE scripts into abstract syntax trees (ASTs) that can be
    compiled into executable plans by the Compiler.

    Example::

        parser = FireParser()
        ast = parser.parse('''
            let swap1 = SwapExactTokensForTokens(amountIn, WETH, USDC, Uniswap)
            let swap2 = SwapExactTokensForTokens(usdcOut, USDC, WETH, Sushiswap)
            swap1 + swap2
        ''')
    """

    def parse(self, source: str) -> ASTNode:
        """Parse a FIRE script into an AST.

        Args:
            source: FIRE script source code.

        Returns:
            Root ASTNode of the parsed script.

        Raises:
            FireParseError: If the script contains syntax errors.
        """
        self._tokenizer = _Tokenizer(source)
        statements: List[ASTNode] = []
        while not self._tokenizer.at_end:
            stmt = self._parse_statement()
            if stmt is not None:
                statements.append(stmt)
        if not statements:
            raise FireParseError("Empty script")
        if len(statements) == 1:
            return statements[0]
        return ASTNode(node_type=NodeType.SEQUENCE, children=statements)

    def _parse_statement(self) -> Optional[ASTNode]:
        """Parse a single statement (let binding or expression)."""
        tok = self._tokenizer.peek()
        if tok is None:
            return None
        if tok[0] == "LET":
            return self._parse_let()
        return self._parse_expression()

    def _parse_let(self) -> ASTNode:
        """Parse: let identifier = expression"""
        self._tokenizer.expect("LET")
        ident_tok = self._tokenizer.expect("IDENT")
        self._tokenizer.expect("EQUALS")
        expr = self._parse_expression()
        return ASTNode(
            node_type=NodeType.ASSIGNMENT,
            identifier=ident_tok[1],
            children=[expr],
        )

    def _parse_expression(self) -> ASTNode:
        """Parse an expression with composition operators (+ and ||)."""
        left = self._parse_unary()
        while True:
            tok = self._tokenizer.peek()
            if tok is None:
                break
            if tok[0] == "PLUS":
                self._tokenizer.advance()
                right = self._parse_unary()
                if left.node_type == NodeType.SEQUENCE:
                    left.children.append(right)
                else:
                    left = ASTNode(node_type=NodeType.SEQUENCE, children=[left, right])
            elif tok[0] == "PARALLEL":
                self._tokenizer.advance()
                right = self._parse_unary()
                if left.node_type == NodeType.PARALLEL:
                    left.children.append(right)
                else:
                    left = ASTNode(node_type=NodeType.PARALLEL, children=[left, right])
            elif tok[0] == "ARROW":
                self._tokenizer.advance()
                right = self._parse_unary()
                # Treat transformation arrows as sequential
                if left.node_type == NodeType.SEQUENCE:
                    left.children.append(right)
                else:
                    left = ASTNode(node_type=NodeType.SEQUENCE, children=[left, right])
            else:
                break
        return left

    def _parse_unary(self) -> ASTNode:
        """Parse unary operators (~, *) and primary expressions."""
        tok = self._tokenizer.peek()
        if tok and tok[0] == "TILDE":
            self._tokenizer.advance()
            operand = self._parse_primary()
            # ~A means reverse operation
            return ASTNode(
                node_type=NodeType.ATOMIC,
                value=f"reverse_{operand.value}" if operand.value else "reverse",
                params=operand.params,
                children=operand.children,
            )
        node = self._parse_primary()
        # Check for repeat operator: operation * n
        tok = self._tokenizer.peek()
        if tok and tok[0] == "STAR":
            self._tokenizer.advance()
            count_tok = self._tokenizer.expect("NUMBER")
            return ASTNode(
                node_type=NodeType.REPEAT,
                count=int(count_tok[1]),
                children=[node],
            )
        return node

    def _parse_primary(self) -> ASTNode:
        """Parse primary expressions: atoms, groups, conditionals, collections."""
        tok = self._tokenizer.peek()
        if tok is None:
            raise FireParseError("Unexpected end of input")

        if tok[0] == "LPAREN":
            self._tokenizer.advance()
            expr = self._parse_expression()
            self._tokenizer.expect("RPAREN")
            return expr

        if tok[0] == "IF":
            return self._parse_conditional()

        if tok[0] == "SIGMA":
            return self._parse_collection()

        if tok[0] == "NUMBER":
            self._tokenizer.advance()
            return ASTNode(node_type=NodeType.LITERAL, value=tok[1])

        if tok[0] == "STRING":
            self._tokenizer.advance()
            return ASTNode(node_type=NodeType.LITERAL, value=tok[1].strip('"'))

        if tok[0] == "HEX":
            self._tokenizer.advance()
            return ASTNode(node_type=NodeType.LITERAL, value=tok[1])

        if tok[0] == "IDENT":
            return self._parse_ident_or_call()

        raise FireParseError(f"Unexpected token: {tok[0]} ('{tok[1]}')", tok[2])

    def _parse_ident_or_call(self) -> ASTNode:
        """Parse an identifier, or a function call if followed by '('."""
        ident_tok = self._tokenizer.advance()
        name = ident_tok[1]

        # Check if this is a function call
        tok = self._tokenizer.peek()
        if tok and tok[0] == "LPAREN":
            self._tokenizer.advance()
            params = self._parse_arg_list()
            self._tokenizer.expect("RPAREN")
            # Build params dict from positional args
            param_dict = {}
            for i, arg in enumerate(params):
                if isinstance(arg, ASTNode) and arg.node_type == NodeType.VARIABLE:
                    param_dict[f"arg{i}"] = arg.value
                elif isinstance(arg, ASTNode) and arg.node_type == NodeType.LITERAL:
                    param_dict[f"arg{i}"] = arg.value
                else:
                    param_dict[f"arg{i}"] = arg
            return ASTNode(
                node_type=NodeType.ATOMIC,
                value=name,
                params=param_dict,
                children=params if any(isinstance(a, ASTNode) for a in params) else [],
            )

        # Plain identifier (variable reference)
        return ASTNode(node_type=NodeType.VARIABLE, value=name)

    def _parse_arg_list(self) -> list:
        """Parse a comma-separated argument list."""
        args = []
        tok = self._tokenizer.peek()
        if tok and tok[0] == "RPAREN":
            return args
        args.append(self._parse_arg())
        while self._tokenizer.match("COMMA"):
            args.append(self._parse_arg())
        return args

    def _parse_arg(self) -> ASTNode:
        """Parse a single argument (can be identifier, number, hex, or string)."""
        tok = self._tokenizer.peek()
        if tok is None:
            raise FireParseError("Unexpected end of input in argument list")
        if tok[0] == "NUMBER":
            self._tokenizer.advance()
            return ASTNode(node_type=NodeType.LITERAL, value=tok[1])
        if tok[0] == "STRING":
            self._tokenizer.advance()
            return ASTNode(node_type=NodeType.LITERAL, value=tok[1].strip('"'))
        if tok[0] == "HEX":
            self._tokenizer.advance()
            return ASTNode(node_type=NodeType.LITERAL, value=tok[1])
        if tok[0] == "IDENT":
            self._tokenizer.advance()
            # Check for compound like "10 ETH" — just treat as ident
            return ASTNode(node_type=NodeType.VARIABLE, value=tok[1])
        raise FireParseError(f"Unexpected token in arg list: {tok[0]}", tok[2])

    def _parse_conditional(self) -> ASTNode:
        """Parse: if condition then script else script"""
        self._tokenizer.expect("IF")
        condition = self._parse_comparison()
        self._tokenizer.expect("THEN")
        if_branch = self._parse_expression()
        self._tokenizer.expect("ELSE")
        else_branch = self._parse_expression()
        return ASTNode(
            node_type=NodeType.CONDITIONAL,
            condition=condition,
            if_branch=if_branch,
            else_branch=else_branch,
        )

    def _parse_comparison(self) -> ASTNode:
        """Parse a comparison expression: expr op expr"""
        left = self._parse_primary()
        tok = self._tokenizer.peek()
        if tok and tok[0] == "COMPARISON":
            self._tokenizer.advance()
            right = self._parse_primary()
            return ASTNode(
                node_type=NodeType.COMPARISON,
                operator=tok[1],
                children=[left, right],
            )
        return left

    def _parse_collection(self) -> ASTNode:
        """Parse: Σ(identifier in collection : script)"""
        self._tokenizer.expect("SIGMA")
        self._tokenizer.expect("LPAREN")
        var_tok = self._tokenizer.expect("IDENT")
        self._tokenizer.expect("IN")
        coll_tok = self._tokenizer.expect("IDENT")
        self._tokenizer.expect("COLON")
        body = self._parse_expression()
        self._tokenizer.expect("RPAREN")
        return ASTNode(
            node_type=NodeType.COLLECTION,
            var_name=var_tok[1],
            collection_name=coll_tok[1],
            children=[body],
        )
