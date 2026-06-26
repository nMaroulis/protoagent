import requests

BASE = "http://127.0.0.1:5000"


def add(title):
    r = requests.post(
        BASE + "/tasks",
        json={"title": title},
    )
    print(r.json())


def list_tasks():
    print(requests.get(BASE + "/tasks").json())


def complete(task_id):
    print(requests.post(f"{BASE}/tasks/{task_id}/complete").json())


if __name__ == "__main__":
    add("Buy milk")
    add("Write report")
    list_tasks()
    complete(1)
    list_tasks()
