from models import Task

_tasks = []
_counter = 1


def create_task(title):
    global _counter

    task = Task(
        id=_counter,
        title=title,
        completed=False,
    )

    _tasks.append(task)
    _counter += 1
    return task


def get_task(task_id):
    for t in _tasks:
        if t.id == task_id:
            return t
    return None


def list_tasks():
    return _tasks


def delete_task(task_id):
    global _tasks
    _tasks = [t for t in _tasks if t.id != task_id]
