from dataclasses import dataclass

@dataclass
class Match:
    start: int
    end: int
    text: str

