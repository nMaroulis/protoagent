def matches_ingredients(recipe, available):
    available = set([i.lower() for i in available])
    recipe_ingredients = set([i.lower() for i in recipe.ingredients])

    # naive match: at least 50% overlap
    if len(recipe_ingredients) == 0:
        return False

    overlap = recipe_ingredients.intersection(available)
    return len(overlap) / len(recipe_ingredients) >= 0.5


def matches_tags(recipe, required_tags):
    if not required_tags:
        return True

    return any(tag in recipe.tags for tag in required_tags)
