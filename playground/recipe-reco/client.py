import requests

BASE = "http://127.0.0.1:5000"


def recommend(ingredients):
    r = requests.post(
        BASE + "/recommend",
        json={"ingredients": ingredients},
    )

    for recipe in r.json():
        print(recipe["name"], "-", recipe["tags"])


if __name__ == "__main__":
    recommend(["tomato", "olive oil", "garlic"])
