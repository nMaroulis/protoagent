import service
import storage
import utils
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.get("/tasks")
def tasks():
    """List all tasks and return them as JSON."""
    return jsonify([utils.serialize(t) for t in storage.list_tasks()])


@app.post("/tasks")
def create():
    """Create a new task from the request body."""
    data = request.json

    task = service.add_task(data["title"])

    return jsonify(utils.serialize(task))


@app.post("/tasks/<int:id>/complete")
def complete(id):
    """Mark a specific task as completed by ID."""
    task = service.complete_task(id)

    if task is None:
        return jsonify({"error": "Not found"}), 404

    return jsonify(utils.serialize(task))


@app.get("/search")
def search():
    """Search for tasks based on a keyword query (q)."""
    keyword = request.args.get("q", "")

    results = service.search(keyword)

    return jsonify([utils.serialize(t) for t in results])


@app.get("/stats")
def stats():
    """Get aggregated statistics about tasks."""
    return jsonify(service.stats())
