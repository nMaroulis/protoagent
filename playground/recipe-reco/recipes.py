import storage


def seed():
    storage.add_recipe(
        "Pasta Pomodoro",
        ["pasta", "tomato", "garlic", "olive oil"],
        ["vegetarian", "quick"],
        ["Boil pasta", "Cook sauce", "Mix"],
    )

    storage.add_recipe(
        "Chicken Curry",
        ["chicken", "curry powder", "rice", "onion"],
        ["high-protein"],
        ["Cook chicken", "Add spices", "Simmer"],
    )

    storage.add_recipe(
        "Avocado Toast",
        ["bread", "avocado", "salt", "lemon"],
        ["vegan", "quick"],
        ["Toast bread", "Mash avocado", "Serve"],
    )

    storage.add_recipe(
        "Greek Salad",
        ["tomato", "cucumber", "feta", "olive oil"],
        ["vegetarian", "healthy"],
        ["Chop veggies", "Mix", "Add feta"],
    )
