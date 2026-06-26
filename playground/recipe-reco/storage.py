from models import Recipe

_recipes = []
_id = 1


def add_recipe(name, ingredients, tags, steps):
    global _id

    recipe = Recipe(
        id=_id,
        name=name,
        ingredients=ingredients,
        tags=tags,
        steps=steps,
    )

    _recipes.append(recipe)
    _id += 1
    return recipe


def get_all():
    return _recipes


def get_by_id(recipe_id):
    for r in _recipes:
        if r.id == recipe_id:
            return r
    return None
