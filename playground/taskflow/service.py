import storage


def add_task(title):
    if len(title.strip()) == 0:
        raise ValueError("Empty title")

    return storage.create_task(title)


def complete_task(task_id):
    task = storage.get_task(task_id)

    if task is None:
        return None

    task.completed = True
    return task


def search(keyword):
    results = []

    for task in storage.list_tasks():
        if keyword.lower() in task.title.lower():
            results.append(task)

    return results


def stats():
    tasks = storage.list_tasks()

    total = len(tasks)

    done = len([t for t in tasks if t.completed])

    return {
        "total": total,
        "done": done,
        "pending": total - done,
    }
