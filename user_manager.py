import json
import os

FILE_NAME = "users.json"


def load_users():

    if not os.path.exists(FILE_NAME):

        with open(FILE_NAME, "w") as f:

            json.dump({"users": []}, f, indent=4)

    with open(FILE_NAME, "r") as f:

        return json.load(f)


def save_users(data):

    with open(FILE_NAME, "w") as f:

        json.dump(data, f, indent=4)


def get_all_users():

    data = load_users()

    return data["users"]


def add_user(user_id, name="Unknown", plan="BASIC"):

    data = load_users()

    for user in data["users"]:

        if user["id"] == user_id:

            return False

    data["users"].append({
        "id": user_id,
        "name": name,
        "plan": plan,
        "active": True
    })

    save_users(data)

    return True


def remove_user(user_id):

    data = load_users()

    data["users"] = [
        user for user in data["users"]
        if user["id"] != user_id
    ]

    save_users(data)


def active_users():

    return [
        user
        for user in get_all_users()
        if user["active"]
    ]