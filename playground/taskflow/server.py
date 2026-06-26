import service
import storage
import utils
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.get("/tasks")
def tasks():
    return jsonify([utils.serialize(t) for t in storage.list_tasks()])


@app.post("/tasks")
def create():
    data = request.json

    task = service.add_task(data["title"])

    return jsonify(utils.serialize(task))


@app.post("/tasks/<int:id>/complete")
def complete(id):
    task = service.complete_task(id)

    if task is None:
        return jsonify({"error": "Not found"}), 404

    return jsonify(utils.serialize(task))


@app.get("/search")
def search():
    keyword = request.args.get("q", "")

    results = service.search(keyword)

    return jsonify([utils.serialize(t) for t in results])


@app.get("/stats")
def stats():
    return jsonify(service.stats())
