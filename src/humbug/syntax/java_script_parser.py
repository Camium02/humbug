from dataclasses import dataclass
from typing import Optional

from humbug.syntax.lexer import Token
from humbug.syntax.parser import Parser
from humbug.syntax.java_script_lexer import JavaScriptLexer


@dataclass
class JavaScriptParserState:
    """
    State information for the JavaScript parser.

    Attributes:
        in_element: Indicates if we're currently parsing an element
    """
    in_element: bool = False


class JavaScriptParser(Parser):
    """
    Parser for JavaScript code.

    This parser processes tokens from the JavaScript lexer and handles special cases
    like function calls and element access.
    """

    def __init__(self) -> None:
        """Initialize the JavaScript parser."""
        super().__init__()
        self._parser_state = JavaScriptParserState()

    def parse(self, parser_state: Optional[JavaScriptParserState], input_str: str) -> JavaScriptParserState:
        """
        Parse the input string using the provided parser state.

        Args:
            parser_state: Optional previous parser state
            input_str: The input string to parse

        Returns:
            The updated parser state after parsing

        Note:
            The parser converts identifier tokens to FUNCTION_OR_METHOD tokens
            when they're followed by parentheses, and to ELEMENT tokens when
            they're part of a dotted access chain.
        """
        self._parser_state.in_element = parser_state.in_element if parser_state else False

        lexer = JavaScriptLexer(input_str)
        lexer.lex()

        while True:
            token = lexer.get_next_token()
            if not token:
                break

            if token.type != 'IDENTIFIER':
                if (token.type == 'OPERATOR' and
                        token.value not in ('.', '?.')):
                    self._parser_state.in_element = False
                    self._tokens.append(token)
                    continue

                if token.type != 'KEYWORD':
                    self._tokens.append(token)
                    continue

                if token.value != 'this' and not self._parser_state.in_element:
                    self._tokens.append(token)
                    continue

            # Look at the next token. If it's a '(' operator then we're making a
            # function or method call!
            cur_in_element = self._parser_state.in_element
            next_token = lexer.peek_next_token(['WHITESPACE'])
            self._parser_state.in_element = cur_in_element

            next_in_element = False
            if next_token and next_token.type == 'OPERATOR':
                if next_token.value == '(':
                    self._parser_state.in_element = False
                    self._tokens.append(Token(
                        type='FUNCTION_OR_METHOD',
                        value=token.value,
                        start=token.start
                    ))
                    continue

                # Is the next token going to be an element?
                if next_token.value in ('.', '?.'):
                    next_in_element = True

            self._parser_state.in_element = next_in_element

            if cur_in_element:
                self._tokens.append(Token(
                    type='ELEMENT',
                    value=token.value,
                    start=token.start
                ))
                continue

            self._tokens.append(token)

        new_parser_state = JavaScriptParserState(
            in_element=self._parser_state.in_element
        )
        return new_parser_state
