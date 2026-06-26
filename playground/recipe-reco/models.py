from dataclasses import dataclass
from typing import List


@dataclass
class Recipe:
    id: int
    name: str
    ingredients: List[str]
    tags: List[str]  # vegan, gluten-free, quick, etc.
    steps: List[str]
