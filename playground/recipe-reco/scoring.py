def score(recipe, available_ingredients):
    available = set([i.lower() for i in available_ingredients])
    recipe_ing = set([i.lower() for i in recipe.ingredients])

    overlap = len(available.intersection(recipe_ing))
    total = len(recipe_ing)

    base_score = overlap / total

    # bonus for quick recipes
    if "quick" in recipe.tags:
        base_score += 0.1

    # bonus for healthy-ish tags
    if "healthy" in recipe.tags:
        base_score += 0.05

    return base_score
