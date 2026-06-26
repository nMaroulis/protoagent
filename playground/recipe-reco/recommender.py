import filters
import scoring
import storage


def recommend(available_ingredients, tags=None, limit=5):
    candidates = storage.get_all()

    results = []

    for recipe in candidates:
        if not filters.matches_ingredients(recipe, available_ingredients):
            continue

        if not filters.matches_tags(recipe, tags):
            continue

        s = scoring.score(recipe, available_ingredients)

        results.append((recipe, s))

    results.sort(key=lambda x: x[1], reverse=True)

    return [r[0] for r in results[:limit]]
