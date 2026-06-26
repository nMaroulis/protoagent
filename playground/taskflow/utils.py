import datetime


def timestamp():
    return datetime.datetime.now().isoformat()


def serialize(task):
    return {
        "id": task.id,
        "title": task.title,
        "completed": task.completed,
    }
