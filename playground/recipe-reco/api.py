import recipes
import recommender
import storage
from flask import Flask, jsonify, request

app = Flask(__name__)

recipes.seed()


@app.post("/recommend")
def recommend():
    data = request.json

    ingredients = data.get("ingredients", [])
    tags = data.get("tags", [])

    results = recommender.recommend(ingredients, tags)

    return jsonify(
        [
            {
                "id": r.id,
                "name": r.name,
                "ingredients": r.ingredients,
                "tags": r.tags,
            }
            for r in results
        ]
    )
